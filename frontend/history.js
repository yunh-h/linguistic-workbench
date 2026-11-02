// SQLite owns snapshots. Only the opt-in preference and legacy import source stay in the browser.
const HISTORY_KEY = "workbench.sentence-history.v1";
const HISTORY_PREF = "workbench.sentence-history.enabled";
const historyState = {
  items: [], cursor: null, sequence: 0, controller: null, loading: false,
  busy: false, exporting: false, clearing: false, queue: Promise.resolve(), unsaved: new Map(),
  versions: [], versionCursor: null, entry: null, versionSequence: 0,
  legacyRaw: null, receipts: [], saved: new Map(), selected: new Set(),
};

function historyRequest(path = "", options = {}, timeout = 12000) {
  return fetchJSON("/api/v0.1/history" + path, options, new AbortController(), timeout,
                   "歷史服務等待逾時，可稍後重新整理或重試保存。");
}
function historyJSON(method, value) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) };
}
function historyControls() {
  $("history-unsaved").hidden = !historyState.unsaved.size;
  $("history-unsaved").textContent = historyState.unsaved.size + " 未保存";
  $("history-clear").disabled = historyState.busy || historyState.exporting || state.pending;
  $("history-import").disabled = historyState.busy || historyState.exporting;
  $("history-remove-legacy").disabled = historyState.busy || historyState.exporting;
  $("history-retry").hidden = !historyState.unsaved.size;
  $("history-retry").disabled = historyState.busy || historyState.exporting;
  $("history-retry").textContent = "重試保存（" + historyState.unsaved.size + "）";
  $("history-more").hidden = !historyState.cursor;
  $("history-more").disabled = historyState.loading;
  for (const button of document.querySelectorAll(".history-delete")) button.disabled = historyState.busy || historyState.exporting || state.pending;
  for (const checkbox of document.querySelectorAll(".history-select input")) checkbox.disabled = historyState.exporting;
  $("history-export-selected").disabled = !historyState.selected.size || historyState.busy || historyState.exporting;
  $("history-export-selected").textContent = "匯出已選紀錄（" + historyState.selected.size + "）";
  $("history-export-all").disabled = historyState.busy || historyState.exporting;
}
function historyMutation(action) {
  const run = async () => {
    historyState.busy = true; historyControls();
    try { await action(); }
    catch (error) { $("history-status").textContent = error.message; }
    finally { historyState.busy = false; historyControls(); }
  };
  historyState.queue = historyState.queue.then(run, run);
  return historyState.queue;
}

async function loadHistory(append = false) {
  if (window.location.protocol === "file:") return;
  historyState.controller?.abort();
  const controller = new AbortController(); historyState.controller = controller;
  const sequence = ++historyState.sequence;
  const params = new URLSearchParams({ limit: "50", q: $("history-search").value });
  if ($("history-language").value) params.set("language", $("history-language").value);
  if (append && historyState.cursor) params.set("cursor", historyState.cursor);
  historyState.loading = true; historyControls();
  try {
    const result = await fetchJSON("/api/v0.1/history?" + params, {}, controller, 12000, "讀取歷史紀錄逾時，請重試。");
    if (sequence !== historyState.sequence) return;
    historyState.items = append ? [...historyState.items, ...result.items] : result.items;
    historyState.cursor = result.next_cursor;
    renderHistory();
  } catch (error) {
    if (sequence === historyState.sequence && error.name !== "AbortError") $("history-status").textContent = error.message;
  } finally {
    if (sequence === historyState.sequence) { historyState.loading = false; historyControls(); }
  }
}

async function persistHistory(payload) {
  const id = payload.analysis.analysis_id;
  try {
    const saved = await historyRequest("", historyJSON("POST", payload));
    historyState.saved.set(id, saved);
    historyState.unsaved.delete(id);
    $("history-status").textContent = "已保存至本機資料庫。";
    await loadHistory();
  } catch (error) {
    historyState.unsaved.set(id, payload);
    $("history-status").textContent = "分析完成，但尚未保存：" + error.message + " 可按重試保存；重新整理頁面會失去未保存的結果。";
  }
  historyControls();
}
function saveHistory(analysis, enrichment) {
  if (!$("history-enabled").checked || historyState.clearing) return;
  const payload = { analysis, enrichment_requested: enrichment };
  historyState.unsaved.set(analysis.analysis_id, payload);
  historyMutation(() => persistHistory(payload));
}

function renderHistory() {
  const box = $("history-list"); box.replaceChildren();
  if (!historyState.items.length) box.append(node("p", "subtle", "沒有符合條件的句子紀錄。"));
  for (const record of historyState.items) {
    const row = node("div", "history-row");
    const label = node("label", "history-select");
    const checkbox = node("input"); checkbox.type = "checkbox";
    checkbox.checked = historyState.selected.has(record.entry_id);
    checkbox.setAttribute("aria-label", "選取紀錄：" + record.original_text);
    checkbox.onchange = () => {
      if (checkbox.checked) historyState.selected.add(record.entry_id);
      else historyState.selected.delete(record.entry_id);
      historyControls();
    };
    label.append(checkbox, node("span", "sr-only", "選取紀錄")); row.append(label);
    const open = node("button", "history-open head-link", record.original_text);
    open.type = "button"; open.onclick = () => openHistory(record);
    const meta = node("span", "subtle", record.language + " · " + new Date(record.observed_at_ms).toLocaleString() +
      " · " + (record.status === "partial" ? "部分結果" : "分析完成") + " · " + record.version_count + " 個版本");
    const remove = node("button", "history-delete head-link", "刪除"); remove.type = "button";
    remove.setAttribute("aria-label", "刪除紀錄：" + record.original_text);
    remove.onclick = () => {
      if (!window.confirm("刪除這個句子及它的所有分析版本？目前列出 " + record.version_count + " 個版本，操作無法復原。")) return;
      historyMutation(async () => {
        const result = await historyRequest("/" + record.entry_id, { method: "DELETE" });
        historyState.selected.delete(record.entry_id);
        for (const [id, saved] of historyState.saved) {
          if (saved.entry_id === record.entry_id) historyState.saved.delete(id);
        }
        for (const [id, payload] of historyState.unsaved) {
          if (payload.analysis.input.language === record.language && payload.analysis.input.text === record.original_text) historyState.unsaved.delete(id);
        }
        if (historyState.entry?.entry_id === record.entry_id) resetHistoryVersions();
        $("history-status").textContent = "已刪除句子及 " + result.deleted_snapshots + " 個分析版本。";
        await loadHistory();
      });
    };
    row.append(open, meta, remove); box.append(row);
  }
  historyControls();
}

async function exportHistory(all) {
  if (historyState.busy || historyState.exporting) return;
  historyState.exporting = true; historyControls();
  const status = $("history-export-status");
  try {
    await historyState.queue;
    const entries = [];
    if (all) {
      let cursor = null;
      do {
        const params = new URLSearchParams({ limit: "100" });
        if (cursor) params.set("cursor", cursor);
        const page = await historyRequest("?" + params);
        entries.push(...page.items); cursor = page.next_cursor;
      } while (cursor);
    } else {
      for (const id of [...historyState.selected]) {
        const entry = await historyRequest("/" + id);
        if (!entry.latest_snapshot) throw new Error("一筆已選紀錄沒有分析版本。");
        entries.push({ entry_id: id, snapshot_id: entry.latest_snapshot.snapshot_id });
      }
    }
    if (!entries.length) throw new Error("沒有可匯出的紀錄。");
    const records = [];
    for (const entry of entries) {
      status.textContent = "讀取紀錄 " + (records.length + 1) + " / " + entries.length + "…";
      const analysis = await historyRequest("/" + entry.entry_id + "/snapshots/" + entry.snapshot_id);
      const translations = await historyRequest("/" + entry.entry_id + "/translations");
      records.push({ analysis, translations: translationMap(translations.items) });
    }
    downloadMarkdown(markdownDocument(records, $("history-export-details").checked));
    status.textContent = "已匯出 " + records.length + " 筆紀錄的最新分析版本。";
  } catch (error) {
    status.textContent = "未完成匯出：" + error.message + "；沒有下載不完整檔案。";
  } finally {
    historyState.exporting = false; historyControls();
  }
}

function resetHistoryVersions() {
  historyState.versionSequence++;
  historyState.entry = null; historyState.versions = []; historyState.versionCursor = null;
  $("history-version-bar").hidden = true;
  $("history-version-select").replaceChildren();
}
async function loadVersions(record, append, versionSequence) {
  const params = new URLSearchParams({ limit: "50" });
  if (append && historyState.versionCursor) params.set("cursor", historyState.versionCursor);
  const result = await historyRequest("/" + record.entry_id + "/snapshots?" + params);
  if (versionSequence !== historyState.versionSequence) return;
  historyState.versions = append ? [...historyState.versions, ...result.items] : result.items;
  historyState.versionCursor = result.next_cursor;
  const selected = $("history-version-select").value;
  const select = $("history-version-select"); select.replaceChildren();
  for (const version of historyState.versions) {
    const option = node("option", "", new Date(version.observed_at_ms).toLocaleString() + " · " +
      (version.status === "partial" ? "部分結果" : "分析完成") + " · " +
      (version.enrichment_requested === "on" ? "有要求模型補充" : "基礎分析"));
    option.value = String(version.snapshot_id); select.append(option);
  }
  if (historyState.versions.some(v => String(v.snapshot_id) === selected)) select.value = selected;
  $("history-versions-more").hidden = !result.next_cursor;
}
async function openHistory(record, snapshot = null) {
  const retainedVersions = snapshot ? historyState.versions : [];
  const retainedCursor = snapshot ? historyState.versionCursor : null;
  invalidateCurrentRequest();
  showPage("analysis");
  const pageSequence = state.pageSequence;
  historyState.entry = record;
  historyState.versions = retainedVersions; historyState.versionCursor = retainedCursor;
  const versionSequence = historyState.versionSequence;
  $("language").value = record.language; $("text").value = record.original_text;
  $("enrichment").checked = (snapshot || record).enrichment_requested === "on"; updateEnrichment();
  const sequence = state.sequence;
  const controller = new AbortController(); state.controller = controller;
  state.pending = true; updateControls();
  showMessage("正在讀取歷史快照…");
  try {
    const snapshotId = snapshot?.snapshot_id || record.snapshot_id;
    const data = await fetchJSON("/api/v0.1/history/" + record.entry_id + "/snapshots/" + snapshotId,
                                {}, controller, 12000, "讀取歷史快照逾時，請重試。");
    if (sequence !== state.sequence) return;
    renderAnalysis(data, state.pageSequence === pageSequence);
    setupMyTranslations(data, { entry_id: record.entry_id, snapshot_id: snapshotId });
    $("result-mode").textContent = data.status === "partial" ? "歷史快照 · 部分結果" : "歷史快照";
    showMessage("已開啟當時的分析快照，尚未重新分析。", "analysis");
    if (!snapshot) await loadVersions(record, false, versionSequence);
    else {
      for (const version of retainedVersions) {
        const option = node("option", "", new Date(version.observed_at_ms).toLocaleString() + " · " + version.status);
        option.value = String(version.snapshot_id); $("history-version-select").append(option);
      }
      $("history-versions-more").hidden = !retainedCursor;
    }
    if (sequence !== state.sequence) return;
    $("history-version-select").value = String(snapshotId);
    $("history-version-bar").hidden = false;
  } catch (error) {
    if (sequence === state.sequence && error.name !== "AbortError") {
      showMessage(error.message, "analysis");
    }
  } finally {
    if (sequence === state.sequence) { state.pending = false; updateControls(); }
  }
}

function inspectLegacy() {
  $("history-import").hidden = true;
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return null;
    if (raw.length * 2 > 2100000) throw new Error();
    const data = JSON.parse(raw);
    if (data.version !== 1 || !Array.isArray(data.records) || data.records.length > 50) throw new Error();
    if (data.records.length) {
      $("history-import").hidden = false;
      $("history-import-status").textContent = "此瀏覽器有 " + data.records.length + " 筆舊紀錄，可匯入本機資料庫。";
    }
    return { raw, records: data.records };
  } catch {
    $("history-import-status").textContent = "無法讀取瀏覽器舊紀錄，可能儲存被停用或資料損壞；原資料未變更。";
    return null;
  }
}
async function importLegacy() {
  const legacy = inspectLegacy();
  if (!legacy?.records.length) return;
  historyState.legacyRaw = null; historyState.receipts = [];
  $("history-remove-legacy").hidden = true;
  const results = [];
  for (let at = 0; at < legacy.records.length; at += 10) {
    const batch = await historyRequest("/import", historyJSON("POST", { records: legacy.records.slice(at, at + 10) }), 65000);
    results.push(...batch.results.map(item => ({ ...item, index: item.index + at })));
  }
  const successful = results.filter(r => r.status === "imported" || r.status === "duplicate");
  const failed = results.filter(r => r.status !== "imported" && r.status !== "duplicate");
  const status = $("history-import-status"); status.replaceChildren();
  status.append(document.createTextNode("匯入完成：" + successful.length + " 筆已保存或已存在，" + failed.length + " 筆未匯入。瀏覽器舊副本仍保留。"));
  for (const item of failed) status.append(node("p", "", "第 " + (item.index + 1) + " 筆：" + item.message));
  if (!failed.length && results.length === legacy.records.length) {
    historyState.legacyRaw = legacy.raw; historyState.receipts = successful;
    $("history-remove-legacy").hidden = false;
  }
  await loadHistory();
}

function initHistory() {
  try { $("history-enabled").checked = localStorage.getItem(HISTORY_PREF) !== "off"; }
  catch { $("history-status").textContent = "無法保存瀏覽器偏好；資料庫紀錄仍可使用。"; }
  inspectLegacy(); loadHistory();
  $("history-enabled").onchange = () => {
    try { localStorage.setItem(HISTORY_PREF, $("history-enabled").checked ? "on" : "off"); }
    catch { $("history-status").textContent = "無法保存偏好；本次分頁仍依目前選項運作。"; }
  };
  $("history-refresh").onclick = () => loadHistory();
  $("history-more").onclick = () => loadHistory(true);
  $("history-search-form").onsubmit = event => { event.preventDefault(); loadHistory(); };
  $("history-language").onchange = () => loadHistory();
  $("history-version-select").onchange = () => {
    const version = historyState.versions.find(v => String(v.snapshot_id) === $("history-version-select").value);
    if (historyState.entry && version) openHistory(historyState.entry, version);
  };
  $("history-versions-more").onclick = async () => {
    const record = historyState.entry, sequence = historyState.versionSequence;
    if (!record) return;
    $("history-versions-more").disabled = true;
    try { await loadVersions(record, true, sequence); }
    catch (error) { if (sequence === historyState.versionSequence) $("message").textContent = error.message; }
    finally { $("history-versions-more").disabled = false; }
  };
  $("history-retry").onclick = () => historyMutation(async () => {
    for (const payload of [...historyState.unsaved.values()]) await persistHistory(payload);
  });
  $("history-import").onclick = () => historyMutation(importLegacy);
  $("history-remove-legacy").onclick = () => {
    if (!window.confirm("刪除此瀏覽器的舊副本？將先確認資料庫中的對應紀錄仍可讀取。")) return;
    historyMutation(async () => {
      if (!historyState.legacyRaw || localStorage.getItem(HISTORY_KEY) !== historyState.legacyRaw) {
        throw new Error("瀏覽器舊紀錄已變更，請重新匯入；未刪除資料。");
      }
      const originals = JSON.parse(historyState.legacyRaw).records;
      for (const receipt of historyState.receipts) {
        const saved = await historyRequest("/" + receipt.entry_id + "/snapshots/" + receipt.snapshot_id);
        if (saved.analysis_id !== originals[receipt.index].id) throw new Error("資料庫中的對應紀錄已變更，未刪除舊副本。");
      }
      if (localStorage.getItem(HISTORY_KEY) !== historyState.legacyRaw) throw new Error("舊紀錄已變更，未刪除資料。");
      localStorage.removeItem(HISTORY_KEY); historyState.legacyRaw = null;
      $("history-remove-legacy").hidden = true; $("history-import").hidden = true;
      $("history-import-status").textContent = "已刪除此瀏覽器的舊副本，資料庫紀錄保留。";
    });
  };
  $("history-clear").onclick = () => {
    if (!window.confirm("清除資料庫中的全部句子與所有分析版本？同一 backend 的其他瀏覽器也會受影響，操作無法復原。")) return;
    historyState.clearing = true; updateControls();
    historyMutation(async () => {
      try {
        const result = await historyRequest("", { method: "DELETE" });
        historyState.unsaved.clear(); historyState.selected.clear(); historyState.saved.clear(); resetHistoryVersions();
        $("history-status").textContent = "已清除 " + result.deleted_entries + " 個句子與 " + result.deleted_snapshots + " 個版本。";
        await loadHistory();
      } finally { historyState.clearing = false; updateControls(); }
    });
  };
  window.addEventListener("focus", () => { if (!historyState.busy) loadHistory(); });
  $("history-export-selected").onclick = () => exportHistory(false);
  $("history-export-all").onclick = () => exportHistory(true);
  window.addEventListener("storage", event => {
    if (event.key === HISTORY_PREF) $("history-enabled").checked = event.newValue !== "off";
    if (event.key === HISTORY_KEY || event.key === null) {
      historyState.legacyRaw = null; historyState.receipts = [];
      $("history-remove-legacy").hidden = true; inspectLegacy();
    }
  });
}
