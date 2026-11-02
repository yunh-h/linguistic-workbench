"""Bounded, restartable per-language processes. No model runs in an HTTP thread.

spawn works on Windows as well as Linux and avoids forking loaded NLP runtimes.
Only one request uses a language worker at a time; lock waits count toward its budget.
"""

from __future__ import annotations

from dataclasses import dataclass
import multiprocessing as mp
from multiprocessing.connection import Connection
import threading
from time import monotonic

from workbench.pipeline import AdapterUnavailable, ParserInvalid, TooManySentences
from workbench.schema import Analysis, Warning
from workbench.llm_config import configured_llm
from workbench.diagnostics import enrichment_warning


class ParserTimeout(TimeoutError):
    pass


@dataclass(frozen=True)
class Budgets:
    parser: float = 8.0
    resources: float = 1.0
    enrichment: float = 20.0
    total: float = 30.0
    startup: float = 60.0

    @classmethod
    def from_env(cls) -> Budgets:
        config = configured_llm()
        enrichment = config.timeout_seconds if config else 20
        return cls(enrichment=enrichment, total=enrichment + 10)


def _worker_main(connection: Connection, language: str) -> None:
    from workbench.pipeline import analyze, get_adapter

    try:
        get_adapter(language).load()
        connection.send(("ready", None))
        while True:
            text, enrichment = connection.recv()

            def checkpoint(stage, analysis):
                connection.send((stage, analysis.model_dump_json()))

            try:
                result = analyze(text, language, enrichment, checkpoint=checkpoint)
                connection.send(("result", result.model_dump_json()))
            except AdapterUnavailable:
                connection.send(("unavailable", None))
            except ParserInvalid:
                connection.send(("invalid", None))
            except TooManySentences:
                connection.send(("too_many_sentences", None))
            except Exception:
                # Never send traceback, prompt, original input, or credentials to the API.
                connection.send(("internal", None))
    except (EOFError, BrokenPipeError, OSError):
        pass
    except Exception:
        try:
            connection.send(("unavailable", None))
        except OSError:
            pass
    finally:
        connection.close()


def _partial(analysis: Analysis, stage: str, enrichment: str, reason: str = "worker") -> Analysis:
    result = analysis.model_copy(deep=True)
    result.status = "partial"
    if stage == "resources":
        result.warnings.append(Warning(
            code="RESOURCE_UNAVAILABLE", message="語言資源處理未完成，已保留基本分析。",
            sentence_id=None, word_ids=[], source_ids=[],
        ))
    if stage == "enrichment" or enrichment == "on":
        result.warnings.append(enrichment_warning(reason if stage == "enrichment" else "skipped"))
    return result


class LanguageWorker:
    def __init__(self, language: str, budgets: Budgets, target=_worker_main):
        self.language = language
        self.budgets = budgets
        self._target = target
        self._context = mp.get_context("spawn")
        self._lock = threading.Lock()
        self._process = None
        self._connection = None
        self._loaded = False
        self._closed = False
        self._recovery = None
        self._recovery_lock = threading.Lock()

    @property
    def ready(self) -> bool:
        process = self._process
        try:
            return bool(self._loaded and process is not None and process.is_alive() and not self._closed)
        except ValueError:  # Concurrent shutdown closed the process handle.
            return False

    def _stop(self) -> None:
        self._loaded = False
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._process is not None:
            if self._process.is_alive():
                self._process.terminate()
            self._process.join(timeout=.2)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=.2)
            if not self._process.is_alive():
                self._process.close()
            self._process = None

    def _receive(self, deadline: float):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ParserTimeout()
        expired = threading.Event()
        process = self._process

        def expire():
            expired.set()
            try:
                process.kill()
            except (ValueError, OSError):
                pass

        # poll alone only bounds waiting for the first byte. The watchdog also
        # bounds recv if a process stalls halfway through writing an IPC frame.
        watchdog = threading.Timer(remaining, expire)
        watchdog.daemon = True
        watchdog.start()
        try:
            if not self._connection.poll(remaining):
                raise ParserTimeout()
            kind, payload = self._connection.recv()
            if kind in {"resources", "enrichment", "result"}:
                try:
                    payload = Analysis.model_validate_json(payload)
                except ValueError as exc:
                    raise ParserInvalid() from exc
            if expired.is_set() or monotonic() > deadline:
                raise ParserTimeout()
            return kind, payload
        except (EOFError, OSError) as exc:
            if expired.is_set():
                raise ParserTimeout() from exc
            raise
        finally:
            watchdog.cancel()
            watchdog.join()

    def _start(self, deadline: float) -> None:
        if self._closed:
            raise AdapterUnavailable()
        if self.ready:
            return
        self._stop()
        parent, child = self._context.Pipe()
        self._connection = parent
        self._process = self._context.Process(target=self._target, args=(child, self.language), daemon=True)
        try:
            self._process.start()
            child.close()
            if self._closed:
                raise AdapterUnavailable()
            kind, _ = self._receive(deadline)
            if kind != "ready":
                raise AdapterUnavailable()
            self._loaded = True
        except BaseException:
            child.close()
            self._stop()
            raise

    def warm(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._start(monotonic() + self.budgets.startup)
            except Exception:
                self._stop()

    def _recover(self) -> None:
        with self._recovery_lock:
            if not self._closed and not self.ready and (self._recovery is None or not self._recovery.is_alive()):
                self._recovery = threading.Thread(target=self.warm, daemon=True)
                self._recovery.start()

    def close(self) -> None:
        self._closed = True
        # Wake a warm-up/recovery that is waiting on a stuck native model loader.
        process = self._process
        if process is not None:
            try:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=.2)
                    if process.is_alive():
                        process.kill()
            except (ValueError, OSError):
                pass
        with self._lock:
            self._stop()
        if self._recovery is not None:
            self._recovery.join(timeout=.5)

    def analyze(self, text: str, enrichment: str) -> Analysis:
        start = monotonic()
        total_deadline = start + self.budgets.total
        stage_deadline = min(total_deadline, start + self.budgets.parser)
        if not self._lock.acquire(timeout=max(0, stage_deadline - monotonic())):
            # A queued request must not kill the request currently using this worker.
            raise ParserTimeout()
        checkpoint = None
        stage = "parser"
        restart = False
        try:
            self._start(stage_deadline)
            self._connection.send((text, enrichment))
            while True:
                kind, payload = self._receive(stage_deadline)
                if kind == "result":
                    return payload
                if kind == "resources" and stage == "parser":
                    stage, checkpoint = kind, payload
                    stage_deadline = min(total_deadline, monotonic() + self.budgets.resources)
                elif kind == "enrichment" and stage == "resources":
                    stage, checkpoint = kind, payload
                    stage_deadline = min(total_deadline, monotonic() + self.budgets.enrichment)
                elif kind == "unavailable":
                    raise AdapterUnavailable()
                elif kind == "invalid":
                    raise ParserInvalid()
                elif kind == "too_many_sentences":
                    raise TooManySentences()
                else:
                    raise RuntimeError("analysis worker failed")
        except (ParserTimeout, EOFError, OSError) as exc:
            self._stop()
            restart = True
            if checkpoint is not None:
                return _partial(checkpoint, stage, enrichment, "timeout" if isinstance(exc, ParserTimeout) else "worker")
            if isinstance(exc, ParserTimeout):
                raise
            raise ParserInvalid() from exc
        except (AdapterUnavailable, RuntimeError):
            self._stop()
            restart = True
            if checkpoint is not None:
                return _partial(checkpoint, stage, enrichment)
            raise
        finally:
            self._lock.release()
            if restart:
                self._recover()


class AnalysisRuntime:
    def __init__(self, budgets: Budgets | None = None):
        self.budgets = budgets or Budgets.from_env()
        self.workers = {language: LanguageWorker(language, self.budgets) for language in ("fr", "ja", "en")}

    def start(self) -> None:
        from workbench.pipeline import ready

        for language, worker in self.workers.items():
            if ready(language):
                worker.warm()

    def close(self) -> None:
        for worker in self.workers.values():
            worker.close()

    def ready(self, language: str) -> bool:
        return language in self.workers and self.workers[language].ready

    def refresh(self) -> None:
        from workbench.pipeline import ready

        for language, worker in self.workers.items():
            if ready(language) and not worker.ready:
                worker._recover()

    def analyze(self, text: str, language: str, enrichment: str) -> Analysis:
        from workbench.pipeline import ready

        if language not in self.workers or not ready(language):
            raise AdapterUnavailable()
        return self.workers[language].analyze(text, enrichment)
