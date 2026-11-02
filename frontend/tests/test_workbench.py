"""Opt-in Chromium acceptance tests against a running parser-only service.

WORKBENCH_TEST_UI=1 pytest frontend/tests -q
Default URL: http://127.0.0.1:8765 (override WORKBENCH_TEST_BASE_URL).
"""

import copy
import os
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(os.getenv("WORKBENCH_TEST_UI") != "1", reason="requires running service and Chromium")
BASE = os.getenv("WORKBENCH_TEST_BASE_URL", "http://127.0.0.1:8765")
ROOT = Path(__file__).resolve().parents[2]
FRENCH = "Je vais au marché."
JAPANESE = "昨日、本を三冊買った。"


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def results(browser):
    context = browser.new_context()
    values = {}
    for language, text in (("fr", FRENCH), ("ja", JAPANESE)):
        response = context.request.post(BASE + "/api/v0.1/analyses", data={
            "text": text, "language": language, "enrichment": "off",
        })
        assert response.status == 200
        values[language] = response.json()
    context.close()
    return values


@pytest.fixture
def page(browser):
    if os.getenv("WORKBENCH_TEST_DB_RESET") != "1":
        pytest.skip("requires explicit WORKBENCH_TEST_DB_RESET=1 and a disposable test server/database")
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    assert context.request.delete(BASE + "/api/v0.1/history").status == 200
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(BASE)
    pw.expect(page.locator("#submit")).to_be_enabled(timeout=20000)
    yield page
    # Background saves must finish before the next case resets the shared test DB.
    page.evaluate("historyState.queue")
    context.close()
    assert errors == []


def show_page(page, name):
    if page.locator("#page-tab-" + name).get_attribute("aria-selected") != "true":
        page.click("#page-tab-" + name)
    pw.expect(page.locator("#page-" + name)).to_be_visible()


def submit(page, language, text):
    show_page(page, "input")
    page.select_option("#language", language)
    page.fill("#text", text)
    page.click("#submit")
    pw.expect(page.locator("#result")).to_be_visible(timeout=15000)


def test_real_french_multiword_head_navigation_and_japanese_mobile(page):
    submit(page, "fr", FRENCH)
    assert page.locator("#analysis-original").text_content() == FRENCH
    au = page.get_by_role("button", name="分析 au", exact=True)
    assert au.count() == 1
    au.click()
    pw.expect(page.locator("#detail h3")).to_have_text("à")
    page.locator(".word-choices").get_by_role("button", name="le", exact=True).click()
    pw.expect(page.locator("#detail h3")).to_have_text("le")
    pw.expect(page.locator("#detail")).to_contain_text("DET")
    page.locator("#detail .head-link").click()
    pw.expect(page.locator("#detail h3")).to_have_text("marché")
    pw.expect(page.locator("#detail")).to_contain_text("stanza-fr-combined")
    au.focus()
    page.keyboard.press("ArrowRight")
    pw.expect(page.get_by_role("button", name="分析 marché", exact=True)).to_be_focused()
    pw.expect(page.get_by_role("button", name="分析 marché", exact=True)).to_have_attribute("aria-pressed", "true")

    submit(page, "ja", JAPANESE)
    assert page.locator("#analysis-original").text_content() == JAPANESE
    page.get_by_role("button", name="分析 本", exact=True).click()
    pw.expect(page.locator("#detail h3")).to_have_text("本")
    pw.expect(page.locator("#detail")).to_contain_text("ja_ginza")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    reading = page.locator(".reading-panel").bounding_box()
    detail = page.locator("#detail").bounding_box()
    assert detail["y"] >= reading["y"] + reading["height"]
    artifact = os.getenv("WORKBENCH_UI_ARTIFACT_DIR")
    if artifact:
        Path(artifact).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(artifact) / "parser-only-mobile.png"), full_page=True)


def test_server_timeout_keeps_input_and_allows_retry(page):
    page.route("**/api/v0.1/analyses", lambda route: route.fulfill(status=504, json={
        "request_id": "fixture", "error": {"code": "PARSER_TIMEOUT", "message": "基本分析逾時，請重試。", "retryable": True},
    }))
    page.fill("#text", FRENCH)
    page.click("#submit")
    pw.expect(page.locator("#message")).to_contain_text("逾時")
    pw.expect(page.locator("#text")).to_have_value(FRENCH)
    pw.expect(page.locator("#submit")).to_be_enabled()
    pw.expect(page.locator("#result")).to_be_hidden()
    page.unroute("**/api/v0.1/analyses")
    page.click("#submit")
    pw.expect(page.locator("#result")).to_be_visible(timeout=15000)


def test_resource_partial_result_remains_interactive(page, results):
    partial = copy.deepcopy(results["fr"])
    partial["status"] = "partial"
    partial["warnings"].append({"code": "RESOURCE_UNAVAILABLE", "message": "語言資源暫時無法使用，已保留基本分析。",
                                "sentence_id": None, "word_ids": [], "source_ids": []})
    page.route("**/api/v0.1/analyses", lambda route: route.fulfill(json=partial))
    submit(page, "fr", FRENCH)
    pw.expect(page.locator("#result-mode")).to_have_text("部分結果")
    pw.expect(page.locator("#warnings")).to_contain_text("語言資源")
    page.get_by_role("button", name="分析 au", exact=True).click()
    pw.expect(page.locator("#detail h3")).to_have_text("à")


def delay_fetch_even_after_abort(page):
    page.evaluate("""() => {
      const original = window.fetch.bind(window);
      window.pendingAnalysis = [];
      window.fetch = (url, options) => url === '/api/v0.1/analyses'
        ? new Promise(resolve => window.pendingAnalysis.push(resolve)) : original(url, options);
    }""")


def resolve_request(page, index, result):
    page.evaluate("""({index, result}) => window.pendingAnalysis[index](new Response(JSON.stringify(result), {
      status: 200, headers: {'Content-Type': 'application/json'}
    }))""", {"index": index, "result": result})


def test_client_deadline_and_late_response_cannot_replace_retry(page, results):
    page.clock.install()
    delay_fetch_even_after_abort(page)
    page.fill("#text", FRENCH)
    page.click("#submit")
    pw.expect(page.locator("#message")).to_have_text("正在分析…")
    page.clock.fast_forward(35001)
    pw.expect(page.locator("#message")).to_contain_text("等待分析逾時")
    pw.expect(page.locator("#submit")).to_be_enabled()
    pw.expect(page.locator("#text")).to_have_value(FRENCH)
    page.select_option("#language", "ja")
    page.fill("#text", JAPANESE)
    page.click("#submit")
    resolve_request(page, 1, results["ja"])
    pw.expect(page.locator("#analysis-original")).to_have_text(JAPANESE)
    resolve_request(page, 0, results["fr"])
    pw.expect(page.locator("#analysis-original")).to_have_text(JAPANESE)


def test_edit_and_language_change_cancel_old_result(page, results):
    delay_fetch_even_after_abort(page)
    page.fill("#text", FRENCH)
    page.click("#submit")
    page.fill("#text", "Elle est arrivée.")
    pw.expect(page.locator("#submit")).to_be_enabled()
    pw.expect(page.locator("#result")).to_be_hidden()
    page.select_option("#language", "ja")
    page.fill("#text", JAPANESE)
    page.click("#submit")
    resolve_request(page, 1, results["ja"])
    pw.expect(page.locator("#analysis-original")).to_have_text(JAPANESE)
    resolve_request(page, 0, results["fr"])
    pw.expect(page.locator("#analysis-original")).to_have_text(JAPANESE)
    show_page(page, "input")
    page.fill("#text", "新しい入力")
    pw.expect(page.locator("#result")).to_be_hidden()


def test_unavailable_service_cannot_be_reenabled_by_typing(page):
    page.route("**/api/v0.1/capabilities", lambda route: route.abort())
    page.click("#refresh-models")
    pw.expect(page.locator("#readiness")).to_contain_text("無法連接")
    page.fill("#text", "Je vais au marché.")
    pw.expect(page.locator("#submit")).to_be_disabled()
    page.unroute("**/api/v0.1/capabilities")
    page.click("#refresh-models")
    pw.expect(page.locator("#submit")).to_be_enabled()
    pw.expect(page.locator("#text")).to_have_value(FRENCH)


def test_input_limit_counts_code_points_and_rejects_locally(page, results):
    received = []

    def respond(route):
        received.append(route.request.post_data_json["text"])
        route.fulfill(json=results["fr"])

    page.route("**/api/v0.1/analyses", respond)
    page.fill("#text", "🍵" * 500)
    page.click("#submit")
    pw.expect(page.locator("#result")).to_be_visible()
    assert len(received) == 1 and len(received[0]) == 500
    show_page(page, "input")
    page.fill("#text", "🍵" * 501)
    page.click("#submit")
    pw.expect(page.locator("#message")).to_contain_text("1–500")
    assert len(received) == 1


def test_direct_file_open_stays_disabled_after_input(browser):
    page = browser.new_page()
    try:
        page.goto((ROOT / "frontend/index.html").as_uri())
        pw.expect(page.locator("#local-file-notice")).to_be_visible()
        page.fill("#text", FRENCH)
        pw.expect(page.locator("#submit")).to_be_disabled()
    finally:
        page.close()


@pytest.mark.parametrize("unknown", [False, True])
def test_validated_enrichment_conflict_shows_sources_without_replacing_parser(page, results, monkeypatch, unknown):
    import json
    monkeypatch.syspath_prepend(str(ROOT / "backend/src"))
    from workbench import enrichment
    from workbench.schema import Analysis, unavailable
    from workbench.validation import validate_analysis

    analysis = Analysis.model_validate(results["fr"])
    sentence = analysis.sentences[0]
    word = next(w for w in sentence.words if w.form == "marché")
    if unknown:
        word.lemma = unavailable("fixture missing lemma")
        analysis.status = "partial"
    candidate = enrichment.EnrichmentDraft.model_validate({
        "translations": [{"sentence_id": sentence.id, "text": "我去市場。"}],
        "glosses": [{"word_id": w.id, "text": "fixture gloss"} for w in sentence.words],
        "constructions": [],
        "proposals": [{"word_id": word.id, "field": "lemma", "value_json": json.dumps("marcher"),
                       "rationale": "<img src=x onerror=alert(1)> 測試候選，不是語言正解。"}],
    })
    monkeypatch.setattr(enrichment, "_request", lambda analysis, repair=False:
                        (candidate, {"model": "fixture-llm", "id": "fixture-response"}))
    enrichment.enrich(analysis)
    validate_analysis(analysis)
    page.route("**/api/v0.1/analyses", lambda route: route.fulfill(json=analysis.model_dump(mode="json")))
    submit(page, "fr", FRENCH)
    page.get_by_role("button", name="分析 marché", exact=True).click()
    conflict = page.locator("#detail .disagreement")
    pw.expect(conflict).to_contain_text("替代分析（待確認）：marcher")
    pw.expect(conflict).to_contain_text("fixture-llm")
    if unknown:
        pw.expect(conflict).to_contain_text("原欄位未判定，不自動補入：此次無法判定")
    else:
        pw.expect(conflict).to_contain_text("保留 parser：marché")
        pw.expect(conflict).to_contain_text("stanza-fr-combined")
    pw.expect(conflict.locator("img")).to_have_count(0)
    pw.expect(conflict).to_contain_text("<img src=x onerror=alert(1)>")
    pw.expect(page.locator("#translations")).to_contain_text("我去市場。")
    pw.expect(page.locator("#detail .gloss")).to_contain_text("fixture gloss")
    assert page.locator("#analysis-original").text_content() == FRENCH


def local_capabilities(page, timeout=105000):
    response = page.request.get(BASE + "/api/v0.1/capabilities")
    payload = response.json()
    payload.update(enrichment_available=True, enrichment_provider="lmstudio", enrichment_model="qwen3-14b",
                   analysis_timeout_ms=timeout, question_available=True, question_timeout_ms=95000)
    page.route("**/api/v0.1/capabilities", lambda route: route.fulfill(json=payload))
    page.click("#refresh-models")
    pw.expect(page.locator("#enrichment")).to_be_enabled()


def test_local_enrichment_uses_advertised_deadline_and_keeps_input(page, results):
    local_capabilities(page)
    pw.expect(page.locator("#enrichment-provider")).to_contain_text("LM Studio · qwen3-14b")
    pw.expect(page.locator("#enrichment-provider")).to_contain_text("連線狀態待分析時確認")
    page.clock.install()
    delay_fetch_even_after_abort(page)
    page.check("#enrichment")
    page.fill("#text", FRENCH)
    page.click("#submit")
    page.clock.fast_forward(35001)
    pw.expect(page.locator("#message")).to_have_text("正在分析…")
    pw.expect(page.locator("#submit")).to_be_disabled()
    page.clock.fast_forward(70000)
    pw.expect(page.locator("#message")).to_contain_text("等待分析逾時")
    pw.expect(page.locator("#text")).to_have_value(FRENCH)
    pw.expect(page.locator("#submit")).to_be_enabled()
    resolve_request(page, 0, results["fr"])
    pw.expect(page.locator("#result")).to_be_hidden()


def test_parser_only_still_has_short_deadline_when_local_provider_is_configured(page):
    local_capabilities(page)
    page.clock.install()
    delay_fetch_even_after_abort(page)
    page.fill("#text", FRENCH)
    page.click("#submit")
    page.clock.fast_forward(35001)
    pw.expect(page.locator("#message")).to_contain_text("等待分析逾時")


def test_untrusted_deadline_cannot_disable_client_timeout(page):
    local_capabilities(page, timeout=999999999)
    page.clock.install()
    delay_fetch_even_after_abort(page)
    page.check("#enrichment")
    page.click("#submit")
    page.clock.fast_forward(35001)
    pw.expect(page.locator("#message")).to_contain_text("等待分析逾時")


def test_diagnostic_details_and_history_restore_without_inference(page, results):
    result = copy.deepcopy(results["fr"])
    result["status"] = "partial"
    result["warnings"].append({"code": "ENRICHMENT_FAILED", "message": "模型已回應，但依存關係候選無效。已保留 parser 分析。",
        "sentence_id": None, "word_ids": [], "source_ids": [],
        "diagnostic": {"reason": "graph", "detail": "候選形成循環，整批未採用。", "retryable": True}})
    calls = []
    def infer(route):
        calls.append(True)
        route.fulfill(json=result)
    page.route("**/api/v0.1/analyses", infer)
    submit(page, "fr", FRENCH)
    page.get_by_text("查看原因與建議").click()
    pw.expect(page.locator("#warnings")).to_contain_text("候選形成循環")
    pw.expect(page.locator(".history-open")).to_have_count(1)
    page.reload()
    pw.expect(page.locator(".history-open")).to_have_count(1)
    show_page(page, "history")
    page.locator(".history-open").click()
    pw.expect(page.locator("#result-mode")).to_have_text("歷史快照 · 部分結果")
    page.get_by_role("button", name="分析 au", exact=True).click()
    pw.expect(page.locator("#detail h3")).to_have_text("à")
    assert len(calls) == 1
    show_page(page, "history")
    page.once("dialog", lambda dialog: dialog.accept())
    show_page(page, "history")
    page.locator(".history-delete").click()
    pw.expect(page.locator(".history-open")).to_have_count(0)
    page.reload()
    pw.expect(page.locator(".history-open")).to_have_count(0)


def test_history_deduplicates_opt_out_and_clear_confirmation(page, results):
    page.route("**/api/v0.1/analyses", lambda route: route.fulfill(json=results["fr"]))
    submit(page, "fr", FRENCH)
    submit(page, "fr", FRENCH)
    pw.expect(page.locator(".history-open")).to_have_count(1)
    show_page(page, "history")
    page.uncheck("#history-enabled")
    page.route("**/api/v0.1/analyses", lambda route: route.fulfill(json=results["ja"]))
    submit(page, "ja", JAPANESE)
    pw.expect(page.locator(".history-open")).to_have_count(1)
    show_page(page, "history")
    page.once("dialog", lambda dialog: dialog.dismiss())
    show_page(page, "history")
    page.click("#history-clear")
    pw.expect(page.locator(".history-open")).to_have_count(1)
    page.once("dialog", lambda dialog: dialog.accept())
    show_page(page, "history")
    page.click("#history-clear")
    pw.expect(page.locator(".history-open")).to_have_count(0)
    page.reload()
    pw.expect(page.locator(".history-open")).to_have_count(0)
    pw.expect(page.locator("#history-enabled")).not_to_be_checked()


def test_corrupt_or_unavailable_storage_does_not_break_analysis(page, results):
    page.evaluate("localStorage.setItem('workbench.sentence-history.v1', '{broken')")
    page.reload()
    pw.expect(page.locator("#history-import-status")).to_contain_text("原資料未變更")
    page.route("**/api/v0.1/analyses", lambda route: route.fulfill(json=results["fr"]))
    submit(page, "fr", FRENCH)
    pw.expect(page.locator(".history-open")).to_have_count(1)
    assert page.evaluate("localStorage.getItem('workbench.sentence-history.v1')") == "{broken"
    page.evaluate("localStorage.removeItem('workbench.sentence-history.v1')")
    page.evaluate("() => { Storage.prototype.setItem = () => { throw new DOMException('blocked', 'QuotaExceededError'); }; }")
    submit(page, "fr", FRENCH)
    pw.expect(page.locator("#history-status")).to_contain_text("已保存至本機資料庫")
    pw.expect(page.locator("#analysis-original")).to_have_text(FRENCH)


def test_personal_translation_and_history_markdown_export(page):
    submit(page, "fr", FRENCH)
    pw.expect(page.locator(".my-translation button")).to_be_enabled()
    page.locator("#my-translation-0").fill("我去市場。")
    page.get_by_role("button", name="保存我的翻譯").click()
    pw.expect(page.get_by_text("已保存。清空並保存可移除個人翻譯。")).to_be_visible()
    with page.expect_download() as download:
        page.locator("#export-analysis").click()
    content = Path(download.value.path()).read_text(encoding="utf-8")
    assert "翻譯（使用者）" in content and "我去市場。" in content

    submit(page, "ja", JAPANESE)
    page.evaluate("historyState.queue")
    show_page(page, "history")
    page.locator("#history-refresh").click()
    first = page.get_by_role("checkbox", name="選取紀錄：" + FRENCH)
    second = page.get_by_role("checkbox", name="選取紀錄：" + JAPANESE)
    pw.expect(first).to_be_visible()
    pw.expect(second).to_be_visible()
    first.check()
    second.check()
    with page.expect_download() as download:
        page.locator("#history-export-selected").click()
    content = Path(download.value.path()).read_text(encoding="utf-8")
    assert FRENCH in content and JAPANESE in content and "我去市場。" in content
    with page.expect_download() as download:
        page.locator("#history-export-all").click()
    content = Path(download.value.path()).read_text(encoding="utf-8")
    assert FRENCH in content and JAPANESE in content


def test_corrupt_snapshot_rejected_without_inference(page, results):
    broken = copy.deepcopy(results["fr"])
    broken["sentences"][0]["words"][0]["dependency"]["value"]["head_word_id"] = "absent"
    page.evaluate("record => localStorage.setItem('workbench.sentence-history.v1', JSON.stringify({version:1, records:[record]}))",
                  {"id": broken["analysis_id"], "saved_at": "2026-01-01T00:00:00Z", "enrichment": "off", "analysis": broken})
    page.reload()
    show_page(page, "history")
    page.click("#history-import")
    pw.expect(page.locator("#history-import-status")).to_contain_text("1 筆未匯入")
    pw.expect(page.locator("#history-remove-legacy")).to_be_hidden()
    assert page.evaluate("localStorage.getItem('workbench.sentence-history.v1')") is not None
    pw.expect(page.locator("#result")).to_be_hidden()


def test_chat_context_plain_text_short_history_and_no_persistence(page, results):
    local_capabilities(page)
    page.route("**/api/v0.1/analyses", lambda route: route.fulfill(json=results["ja"]))
    submit(page, "ja", JAPANESE)
    page.get_by_role("button", name="分析 を", exact=True).click()
    seen = []
    answer = "<img src=x onerror=alert(1)> 模型解釋"
    def respond(route):
        seen.append(route.request.post_data_json)
        route.fulfill(json={"answer": answer, "model": "fixture-local", "provider": "lmstudio"})
    page.route("**/api/v0.1/questions", respond)
    for i in range(5):
        page.fill("#question-input", f"問題 {i}")
        page.click("#question-submit")
        pw.expect(page.locator("#question-status")).to_contain_text("fixture-local")
        pw.expect(page.locator("#question-submit")).to_be_enabled()
    assert seen[0]["selected_word_id"] == next(w["id"] for w in results["ja"]["sentences"][0]["words"] if w["form"] == "を")
    assert [len(r["history"]) for r in seen] == [0, 2, 4, 6, 6]
    assert seen[-1]["analysis"] == results["ja"]
    pw.expect(page.locator("#question-messages img")).to_have_count(0)
    pw.expect(page.locator("#question-messages")).to_contain_text(answer)
    pw.expect(page.locator(".chat-turn")).to_have_count(6)
    pw.expect(page.locator(".history-open")).to_have_count(1)
    record = page.request.get(BASE + "/api/v0.1/history").json()["items"][0]
    stored = page.request.get(BASE + f"/api/v0.1/history/{record['entry_id']}/snapshots/{record['snapshot_id']}").text()
    assert "模型解釋" not in stored and "問題 0" not in stored
    assert page.evaluate("localStorage.getItem('workbench.sentence-history.v1')") is None
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.reload()
    pw.expect(page.locator(".chat-turn")).to_have_count(0)
    pw.expect(page.locator(".history-open")).to_have_count(1)


def test_chat_failure_keeps_question_and_analysis(page, results):
    local_capabilities(page)
    page.route("**/api/v0.1/analyses", lambda route: route.fulfill(json=results["fr"]))
    submit(page, "fr", FRENCH)
    page.route("**/api/v0.1/questions", lambda route: route.fulfill(status=502, json={
        "error": {"message": "模型輸出被截斷", "detail": "請縮短輸入"}}))
    page.fill("#question-input", "這個詞是什麼？")
    page.click("#question-submit")
    pw.expect(page.locator("#question-status")).to_contain_text("模型輸出被截斷 請縮短輸入")
    pw.expect(page.locator("#question-input")).to_have_value("這個詞是什麼？")
    pw.expect(page.locator("#analysis-original")).to_have_text(FRENCH)
    pw.expect(page.locator("#question-submit")).to_be_enabled()


def test_chat_timeout_and_switching_sentence_discard_late_answers(page, results):
    local_capabilities(page)
    page.route("**/api/v0.1/analyses", lambda route: route.fulfill(json=results["fr"]))
    submit(page, "fr", FRENCH)
    page.clock.install()
    page.evaluate("""() => {
      const original = window.fetch.bind(window);
      window.pendingQuestions = [];
      window.fetch = (url, options) => url === '/api/v0.1/questions'
        ? new Promise(resolve => window.pendingQuestions.push(resolve)) : original(url, options);
    }""")
    page.fill("#question-input", "問題")
    page.click("#question-submit")
    page.clock.fast_forward(95001)
    pw.expect(page.locator("#question-status")).to_contain_text("逾時")
    pw.expect(page.locator("#question-input")).to_have_value("問題")
    page.click("#question-submit")
    show_page(page, "input")
    page.fill("#text", "新句子")
    page.evaluate("""() => pendingQuestions.forEach(resolve => resolve(new Response(JSON.stringify({answer:'舊回答',model:'fixture'}),{status:200})))""")
    pw.expect(page.locator(".chat-turn")).to_have_count(0)
    pw.expect(page.locator("#result")).to_be_hidden()
    pw.expect(page.locator("#submit")).to_be_enabled()


def test_history_pagination_search_and_other_browser(page, results):
    for i in range(52):
        value = copy.deepcopy(results["fr"])
        value["analysis_id"] = f"page-{i}"
        value["input"]["text"] += " " * i
        assert page.request.post(BASE + "/api/v0.1/history", data={"analysis": value, "enrichment_requested": "off"}).status == 201
    show_page(page, "history")
    page.click("#history-refresh")
    pw.expect(page.locator(".history-open")).to_have_count(50)
    show_page(page, "history")
    page.click("#history-more")
    pw.expect(page.locator(".history-open")).to_have_count(52)
    other_context = page.context.browser.new_context()
    other = other_context.new_page()
    other.goto(BASE + "#history")
    pw.expect(other.locator(".history-open")).to_have_count(50)
    other_context.close()
    assert page.request.post(BASE + "/api/v0.1/history", data={"analysis": results["ja"], "enrichment_requested": "off"}).status == 201
    page.select_option("#history-language", "ja")
    pw.expect(page.locator(".history-open")).to_have_count(1)
    page.fill("#history-search", "not in text")
    page.locator("#history-search-form").get_by_role("button", name="搜尋", exact=True).click()
    pw.expect(page.locator(".history-open")).to_have_count(0)


def test_cancelled_analysis_is_not_added_to_history(page, results):
    delay_fetch_even_after_abort(page)
    page.fill("#text", FRENCH)
    page.click("#submit")
    page.fill("#text", "換句子")
    resolve_request(page, 0, results["fr"])
    pw.expect(page.locator(".history-open")).to_have_count(0)
    assert page.evaluate("localStorage.getItem('workbench.sentence-history.v1')") is None
    assert page.request.get(BASE + "/api/v0.1/history").json()["items"] == []


def test_history_versions_keep_complete_before_partial(page, results):
    first = copy.deepcopy(results["fr"])
    second = copy.deepcopy(first)
    second.update(analysis_id="second-version", status="partial")
    responses = [first, second]
    page.route("**/api/v0.1/analyses", lambda route: route.fulfill(json=responses.pop(0)))
    submit(page, "fr", FRENCH)
    show_page(page, "history")
    pw.expect(page.locator(".history-row")).to_contain_text("1 個版本")
    submit(page, "fr", FRENCH)
    show_page(page, "history")
    pw.expect(page.locator(".history-row")).to_contain_text("2 個版本")
    show_page(page, "history")
    page.locator(".history-open").click()
    pw.expect(page.locator("#history-version-select option")).to_have_count(2)
    pw.expect(page.locator("#result-mode")).to_have_text("歷史快照 · 部分結果")
    page.select_option("#history-version-select", index=1)
    pw.expect(page.locator("#result-mode")).to_have_text("歷史快照")
    page.get_by_role("button", name="分析 au", exact=True).click()
    pw.expect(page.locator("#detail h3")).to_have_text("à")
    pw.expect(page.locator("#history-version-select option")).to_have_count(2)


def test_database_save_failure_retry_keeps_analysis(page, results):
    page.route("**/api/v0.1/analyses", lambda route: route.fulfill(json=results["fr"]))
    def unavailable(route):
        if route.request.method == "POST":
            route.fulfill(status=503, json={"error": {"message": "歷史資料庫暫時無法使用。"}})
        else:
            route.continue_()
    page.route("**/api/v0.1/history", unavailable)
    submit(page, "fr", FRENCH)
    pw.expect(page.locator("#history-status")).to_contain_text("分析完成，但尚未保存")
    pw.expect(page.locator("#analysis-original")).to_have_text(FRENCH)
    pw.expect(page.locator("#history-unsaved")).to_have_text("1 未保存")
    show_page(page, "history")
    pw.expect(page.locator("#history-retry")).to_be_visible()
    page.unroute("**/api/v0.1/history", unavailable)
    show_page(page, "history")
    page.click("#history-retry")
    pw.expect(page.locator(".history-open")).to_have_count(1)
    pw.expect(page.locator("#history-retry")).to_be_hidden()
    pw.expect(page.locator("#history-unsaved")).to_be_hidden()
    page.reload()
    pw.expect(page.locator(".history-open")).to_have_count(1)


def test_legacy_import_is_repeatable_and_cleanup_is_explicit(page, results):
    record = {"id": results["fr"]["analysis_id"], "saved_at": "2026-01-01T00:00:00Z", "enrichment": "off", "analysis": results["fr"]}
    page.evaluate("record => localStorage.setItem('workbench.sentence-history.v1', JSON.stringify({version:1, records:[record]}))", record)
    page.reload()
    show_page(page, "history")
    pw.expect(page.locator("#history-import")).to_be_visible()
    show_page(page, "history")
    page.click("#history-import")
    pw.expect(page.locator("#history-import-status")).to_contain_text("1 筆已保存或已存在，0 筆未匯入")
    pw.expect(page.locator(".history-row")).to_contain_text("1 個版本")
    assert page.evaluate("localStorage.getItem('workbench.sentence-history.v1')") is not None
    show_page(page, "history")
    page.click("#history-import")
    pw.expect(page.locator(".history-row")).to_contain_text("1 個版本")
    page.once("dialog", lambda dialog: dialog.accept())
    show_page(page, "history")
    page.click("#history-remove-legacy")
    pw.expect(page.locator("#history-import-status")).to_contain_text("已刪除此瀏覽器的舊副本")
    assert page.evaluate("localStorage.getItem('workbench.sentence-history.v1')") is None
    page.reload()
    pw.expect(page.locator(".history-open")).to_have_count(1)


def test_legacy_cleanup_refuses_if_database_copy_is_gone(page, results):
    record = {"id": results["fr"]["analysis_id"], "saved_at": "2026-01-01T00:00:00Z", "enrichment": "off", "analysis": results["fr"]}
    page.evaluate("record => localStorage.setItem('workbench.sentence-history.v1', JSON.stringify({version:1, records:[record]}))", record)
    page.reload()
    show_page(page, "history")
    page.click("#history-import")
    show_page(page, "history")
    pw.expect(page.locator("#history-remove-legacy")).to_be_visible()
    assert page.request.delete(BASE + "/api/v0.1/history").status == 200
    page.once("dialog", lambda dialog: dialog.accept())
    show_page(page, "history")
    page.click("#history-remove-legacy")
    pw.expect(page.locator("#history-status")).to_contain_text("找不到")
    assert page.evaluate("localStorage.getItem('workbench.sentence-history.v1')") is not None


def test_committed_save_with_lost_response_retries_without_duplicate_version(page, results):
    page.route("**/api/v0.1/analyses", lambda route: route.fulfill(json=results["fr"]))
    statuses = []

    def commit_then_drop_first_response(route):
        if route.request.method != "POST":
            return route.continue_()
        response = route.fetch()
        statuses.append(response.status)
        if len(statuses) == 1:
            route.abort("connectionreset")
        else:
            route.fulfill(response=response)

    page.route("**/api/v0.1/history", commit_then_drop_first_response)
    submit(page, "fr", FRENCH)
    pw.expect(page.locator("#history-status")).to_contain_text("分析完成，但尚未保存")
    pw.expect(page.locator("#analysis-original")).to_have_text(FRENCH)
    persisted = page.request.get(BASE + "/api/v0.1/history").json()["items"]
    assert len(persisted) == 1 and persisted[0]["version_count"] == 1
    show_page(page, "history")
    page.click("#history-retry")
    pw.expect(page.locator("#history-status")).to_have_text("已保存至本機資料庫。")
    pw.expect(page.locator("#history-retry")).to_be_hidden()
    pw.expect(page.locator(".history-row")).to_contain_text("1 個版本")
    assert statuses == [201, 200]
    assert page.request.get(BASE + "/api/v0.1/history").json()["items"] == persisted
    page.reload()
    pw.expect(page.locator(".history-row")).to_contain_text("1 個版本")


@pytest.mark.parametrize("second_batch_committed", [False, True])
def test_interrupted_multibatch_import_resumes_without_duplicates_or_losing_original(page, results, second_batch_committed):
    records = []
    for index in range(21):
        value = copy.deepcopy(results["fr"])
        value["analysis_id"] = f"interrupted-import-{index}"
        records.append({"id": value["analysis_id"], "saved_at": "2026-01-01T00:00:00Z",
                        "enrichment": "off", "analysis": value})
    page.evaluate("records => localStorage.setItem('workbench.sentence-history.v1', JSON.stringify({version:1,records}))", records)
    original = page.evaluate("localStorage.getItem('workbench.sentence-history.v1')")
    page.reload()
    batches = []

    def interrupt_second_batch(route):
        batches.append(len(route.request.post_data_json["records"]))
        if len(batches) == 2:
            if second_batch_committed:
                assert route.fetch().status == 200
            route.abort("connectionreset")
        else:
            route.continue_()

    page.route("**/api/v0.1/history/import", interrupt_second_batch)
    show_page(page, "history")
    page.click("#history-import")
    pw.expect(page.locator("#history-status")).to_contain_text("無法連接")
    pw.expect(page.locator("#history-import")).to_be_enabled()
    pw.expect(page.locator("#history-remove-legacy")).to_be_hidden()
    assert batches == [10, 10]
    items = page.request.get(BASE + "/api/v0.1/history").json()["items"]
    assert len(items) == 1 and items[0]["version_count"] == (20 if second_batch_committed else 10)
    assert page.evaluate("localStorage.getItem('workbench.sentence-history.v1')") == original
    page.unroute("**/api/v0.1/history/import", interrupt_second_batch)
    show_page(page, "history")
    page.click("#history-import")
    pw.expect(page.locator("#history-import-status")).to_contain_text("21 筆已保存或已存在，0 筆未匯入")
    pw.expect(page.locator(".history-row")).to_contain_text("21 個版本")
    show_page(page, "history")
    pw.expect(page.locator("#history-remove-legacy")).to_be_visible()
    assert page.evaluate("localStorage.getItem('workbench.sentence-history.v1')") == original
    versions = page.request.get(BASE + f"/api/v0.1/history/{items[0]['entry_id']}/snapshots").json()["items"]
    assert len(versions) == 21
    assert {v["analysis_id"] for v in versions} == {r["id"] for r in records}


def save_history_fixture(page, value):
    response = page.request.post(BASE + "/api/v0.1/history", data={"analysis": value, "enrichment_requested": "off"})
    assert response.status == 201
    return response.json()


def delay_history_fetch(page, prefix):
    # Deliberately ignore AbortSignal: even an uncancellable late response must lose.
    page.evaluate("""prefix => {
      const original = window.fetch.bind(window);
      window.pendingHistory = [];
      window.fetch = (url, options) => String(url).startsWith(prefix)
        ? new Promise(resolve => window.pendingHistory.push({url: String(url), resolve}))
        : original(url, options);
    }""", prefix)


def resolve_history_fetch(page, index, result):
    page.evaluate("""async ({index, result}) => {
      window.pendingHistory[index].resolve(new Response(JSON.stringify(result), {
        status: 200, headers: {'Content-Type': 'application/json'}
      }));
      // Drain the fetch/text/JSON/render continuations before asserting unchanged UI.
      await new Promise(resolve => setTimeout(resolve, 0));
    }""", {"index": index, "result": result})


def test_late_history_search_cannot_replace_latest_language_and_query(page, results):
    for language in ("fr", "ja"):
        save_history_fixture(page, results[language])
    show_page(page, "history")
    page.click("#history-refresh")
    pw.expect(page.locator(".history-open")).to_have_count(2)
    delay_history_fetch(page, "/api/v0.1/history?")
    page.fill("#history-search", "marché")
    page.locator("#history-search-form").get_by_role("button", name="搜尋", exact=True).click()
    page.fill("#history-search", "本")
    page.select_option("#history-language", "ja")
    page.wait_for_function("pendingHistory.length === 2")
    urls = page.evaluate("pendingHistory.map(item => item.url)")
    older = page.request.get(BASE + urls[0]).json()
    newer = page.request.get(BASE + urls[1]).json()
    assert [r["language"] for r in older["items"]] == ["fr"]
    assert [r["language"] for r in newer["items"]] == ["ja"]
    resolve_history_fetch(page, 1, newer)
    pw.expect(page.locator(".history-open")).to_have_text([JAPANESE])
    resolve_history_fetch(page, 0, older)
    pw.expect(page.locator(".history-open")).to_have_text([JAPANESE])
    pw.expect(page.locator("#history-language")).to_have_value("ja")
    pw.expect(page.locator("#history-search")).to_have_value("本")


def test_late_old_snapshot_cannot_replace_reopened_latest_version(page, results):
    first = save_history_fixture(page, results["fr"])
    latest = copy.deepcopy(results["fr"])
    latest.update(analysis_id="newest-race-version", status="partial")
    second = save_history_fixture(page, latest)
    show_page(page, "history")
    page.click("#history-refresh")
    pw.expect(page.locator(".history-row")).to_contain_text("2 個版本")
    show_page(page, "history")
    page.locator(".history-open").click()
    pw.expect(page.locator("#history-version-select option")).to_have_count(2)
    delay_history_fetch(page, f"/api/v0.1/history/{first['entry_id']}/snapshots/{first['snapshot_id']}")
    page.select_option("#history-version-select", str(first["snapshot_id"]))
    page.wait_for_function("pendingHistory.length === 1")
    show_page(page, "history")
    page.locator(".history-open").click()
    pw.expect(page.locator("#history-version-bar")).to_be_visible()
    pw.expect(page.locator("#history-version-select")).to_have_value(str(second["snapshot_id"]))
    resolve_history_fetch(page, 0, results["fr"])
    pw.expect(page.locator("#result-mode")).to_have_text("歷史快照 · 部分結果")
    pw.expect(page.locator("#history-version-select")).to_have_value(str(second["snapshot_id"]))
    assert page.evaluate("state.analysis.analysis_id") == latest["analysis_id"]


def test_late_version_list_cannot_replace_other_sentence_versions(page, results):
    french = save_history_fixture(page, results["fr"])
    japanese = save_history_fixture(page, results["ja"])
    show_page(page, "history")
    page.click("#history-refresh")
    pw.expect(page.locator(".history-open")).to_have_count(2)
    prefix = f"/api/v0.1/history/{french['entry_id']}/snapshots?"
    delay_history_fetch(page, prefix)
    show_page(page, "history")
    page.get_by_role("button", name=FRENCH, exact=True).click()
    page.wait_for_function("pendingHistory.length === 1")
    show_page(page, "history")
    page.get_by_role("button", name=JAPANESE, exact=True).click()
    pw.expect(page.locator("#history-version-bar")).to_be_visible()
    pw.expect(page.locator("#history-version-select")).to_have_value(str(japanese["snapshot_id"]))
    older = page.request.get(BASE + prefix + "limit=50").json()
    resolve_history_fetch(page, 0, older)
    pw.expect(page.locator("#analysis-original")).to_have_text(JAPANESE)
    pw.expect(page.locator("#history-version-select")).to_have_value(str(japanese["snapshot_id"]))
    assert page.locator("#history-version-select option").evaluate_all("options => options.map(o => o.value)") == [str(japanese["snapshot_id"])]


def test_history_deleted_by_other_browser_reports_missing_without_inference(page, results):
    saved = save_history_fixture(page, results["fr"])
    show_page(page, "history")
    page.click("#history-refresh")
    pw.expect(page.locator(".history-open")).to_have_count(1)
    inference_requests = []
    page.on("request", lambda request: inference_requests.append(request.url)
            if request.url.endswith(("/analyses", "/questions")) else None)
    other = page.context.browser.new_context()
    try:
        assert other.request.delete(BASE + f"/api/v0.1/history/{saved['entry_id']}").status == 200
    finally:
        other.close()
    show_page(page, "history")
    page.locator(".history-open").click()
    pw.expect(page.locator("#message")).to_contain_text("找不到")
    pw.expect(page.locator("#result")).to_be_hidden()
    pw.expect(page.locator("#submit")).to_be_enabled()
    pw.expect(page.locator("#text")).to_have_value(FRENCH)
    show_page(page, "history")
    page.click("#history-refresh")
    pw.expect(page.locator(".history-open")).to_have_count(0)
    assert inference_requests == []


def test_reading_tabs_show_actual_dependencies_and_support_keyboard(page, results):
    value = results["fr"]
    page.route("**/api/v0.1/analyses", lambda route: route.fulfill(json=value))
    submit(page, "fr", FRENCH)
    words = [w for sentence in value["sentences"] for w in sentence["words"]]
    surfaces = [s["surface"] for sentence in value["sentences"] for s in sentence["segments"]]
    assert page.locator(".segment-form").all_text_contents() == surfaces
    assert page.locator("#analysis-original").text_content() == value["input"]["text"]
    page.locator("#tab-words").focus()
    page.keyboard.press("ArrowRight")
    pw.expect(page.locator("#tab-syntax")).to_be_focused()
    pw.expect(page.locator("#tab-syntax")).to_have_attribute("aria-selected", "true")
    pw.expect(page.locator("#view-words")).to_be_hidden()
    pw.expect(page.locator("#syntax-rows tr")).to_have_count(len(words))
    for i, word in enumerate(words):
        pw.expect(page.locator("#syntax-rows tr").nth(i).locator("td").nth(1)).to_have_text(word["dependency"]["value"]["relation"])
    first = words[0]
    head_id = first["dependency"]["value"]["head_word_id"]
    head = next(w for w in words if w["id"] == head_id)
    page.locator("#syntax-rows tr").first.locator("td").nth(2).get_by_role("button").click()
    pw.expect(page.locator("#detail h3")).to_have_text(head["form"])
    page.locator("#tab-syntax").focus()
    page.keyboard.press("End")
    pw.expect(page.locator("#view-grammar")).to_be_visible()
    if not any(a["kind"] == "construction" for a in value["annotations"]):
        pw.expect(page.locator("#constructions")).to_contain_text("沒有構式註解")
    page.keyboard.press("Home")
    pw.expect(page.locator("#view-words")).to_be_visible()
    page.click("#edit-original")
    pw.expect(page.locator("#text")).to_be_focused()
    assert page.evaluate("state.analysis.analysis_id") == value["analysis_id"]


def test_source_drawer_uses_real_sources_and_returns_focus(page, results):
    value = copy.deepcopy(results["fr"])
    value["sources"][0]["name"] = '<img src=x onerror="window.sourceInjected=true">'
    page.route("**/api/v0.1/analyses", lambda route: route.fulfill(json=value))
    submit(page, "fr", FRENCH)
    pw.expect(page.locator("#word-source")).to_be_disabled()
    page.click("#analysis-source")
    pw.expect(page.locator("#source-dialog")).to_be_visible()
    pw.expect(page.locator("#source-evidence .evidence")).to_have_count(len(value["sources"]))
    pw.expect(page.locator("#source-evidence")).to_contain_text(value["sources"][0]["name"])
    assert page.locator("#source-evidence img").count() == 0
    assert page.evaluate("window.sourceInjected === undefined")
    page.keyboard.press("Escape")
    pw.expect(page.locator("#source-dialog")).to_be_hidden()
    pw.expect(page.locator("#analysis-source")).to_be_focused()
    page.get_by_role("button", name="分析 au", exact=True).click()
    page.click("#word-source")
    pw.expect(page.locator("#source-context")).to_contain_text("選取詞語：à")
    page.click("#close-source")
    pw.expect(page.locator("#word-source")).to_be_focused()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_editorial_layout_mobile_and_color_schemes(page, results, scheme):
    page.emulate_media(color_scheme=scheme, reduced_motion="reduce")
    page.route("**/api/v0.1/analyses", lambda route: route.fulfill(json=results["ja"]))
    submit(page, "ja", JAPANESE)
    page.get_by_role("button", name="分析 本", exact=True).click()
    page.locator(".language-details summary").click()
    pw.expect(page.locator(".language-details")).to_contain_text("ホン")
    page.locator(".language-details summary").click()
    for width in (1280, 760, 390, 320):
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        reading = page.locator(".reading-panel").bounding_box()
        inspector = page.locator(".inspector").bounding_box()
        if width <= 760:
            assert inspector["y"] >= reading["y"] + reading["height"]
        else:
            assert inspector["x"] >= reading["x"] + reading["width"] - 1
    page.click("#analysis-source")
    assert page.locator("#source-dialog").bounding_box()["width"] <= 320
    page.keyboard.press("Escape")
    page.click("#tab-syntax")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    artifact = os.getenv("WORKBENCH_UI_ARTIFACT_DIR")
    if artifact:
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(Path(artifact) / f"editorial-{scheme}-mobile.png"), full_page=True)
        page.set_viewport_size({"width": 1280, "height": 1000})
        page.click("#tab-words")
        page.locator("#result").screenshot(path=str(Path(artifact) / f"editorial-{scheme}-analysis.png"))


def expect_only_page(page, selected):
    for name in ("input", "analysis", "history"):
        panel = page.locator("#page-" + name)
        if name == selected:
            pw.expect(panel).to_be_visible()
        else:
            pw.expect(panel).to_be_hidden()
        pw.expect(page.locator("#page-tab-" + name)).to_have_attribute("aria-selected", str(name == selected).lower())


def test_main_pages_are_separate_and_back_preserves_state(page, results):
    expect_only_page(page, "input")
    show_page(page, "analysis")
    expect_only_page(page, "analysis")
    pw.expect(page.locator("#analysis-empty")).to_be_visible()
    page.locator("[data-open-input]").click()
    expect_only_page(page, "input")
    page.route("**/api/v0.1/analyses", lambda route: route.fulfill(json=results["fr"]))
    submit(page, "fr", FRENCH)
    expect_only_page(page, "analysis")
    page.get_by_role("button", name="分析 au", exact=True).click()
    page.click("#edit-original")
    expect_only_page(page, "input")
    pw.expect(page.locator("#text")).to_have_value(FRENCH)
    show_page(page, "history")
    expect_only_page(page, "history")
    page.fill("#history-search", "marché")
    show_page(page, "analysis")
    pw.expect(page.locator("#detail h3")).to_have_text("à")
    page.go_back()
    expect_only_page(page, "history")
    pw.expect(page.locator("#history-search")).to_have_value("marché")
    page.go_back()
    expect_only_page(page, "input")
    pw.expect(page.locator("#text")).to_have_value(FRENCH)
    page.go_forward()
    expect_only_page(page, "history")
    page.reload()
    expect_only_page(page, "history")
    show_page(page, "analysis")
    pw.expect(page.locator("#analysis-empty")).to_be_visible()


@pytest.mark.parametrize("request_kind", ["analysis", "snapshot"])
def test_late_result_does_not_navigate_away_from_chosen_page(page, results, request_kind):
    if request_kind == "analysis":
        delay_fetch_even_after_abort(page)
        page.fill("#text", FRENCH)
        page.click("#submit")
        show_page(page, "history")
        resolve_request(page, 0, results["fr"])
        chosen_page = "history"
    else:
        saved = save_history_fixture(page, results["fr"])
        show_page(page, "history")
        page.click("#history-refresh")
        pw.expect(page.locator(".history-open")).to_have_count(1)
        delay_history_fetch(page, f"/api/v0.1/history/{saved['entry_id']}/snapshots/{saved['snapshot_id']}")
        page.locator(".history-open").click()
        expect_only_page(page, "analysis")
        page.wait_for_function("pendingHistory.length === 1")
        show_page(page, "input")
        resolve_history_fetch(page, 0, results["fr"])
        chosen_page = "input"
    page.wait_for_function("state.analysis !== null && !state.pending")
    expect_only_page(page, chosen_page)
    show_page(page, "analysis")
    expect_only_page(page, "analysis")
    pw.expect(page.locator("#analysis-original")).to_have_text(FRENCH)
