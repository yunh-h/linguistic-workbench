const $ = (id) => document.getElementById(id);
const state = { analysis: null, selected: null, sequence: 0, controller: null, capabilities: null, pending: false, checking: true,
  page: "input", pageSequence: 0 };
const ANALYSIS_TIMEOUT_MS = 35000;
let currentTranslations = new Map();
const codepoints = (text) => Array.from(text);
const slice = (text, start, end) => codepoints(text).slice(start, end).join("");

function syncPage() {
  for (const tab of document.querySelectorAll("[data-page]")) {
    const active = tab.dataset.page === state.page;
    tab.setAttribute("aria-selected", String(active)); tab.tabIndex = active ? 0 : -1;
    $("page-" + tab.dataset.page).hidden = !active;
  }
  $("result").hidden = !state.analysis;
  $("analysis-empty").hidden = !!state.analysis || state.pending;
  $("analysis-loading").hidden = !!state.analysis || !state.pending;
  $("message").hidden = $("message").dataset.page !== state.page;
}

function showPage(page, { writeHistory = true, focus = false } = {}) {
  if (!["input", "analysis", "history"].includes(page)) page = "input";
  const changed = state.page !== page;
  if (changed) state.pageSequence++;
  state.page = page;
  $("source-dialog").close();
  if (writeHistory && location.hash !== "#" + page) window.history.pushState(null, "", "#" + page);
  syncPage();
  if (changed) window.scrollTo(0, 0);
  if (focus) {
    const target = page === "input" ? $("text") : $("page-tab-" + page);
    target.focus({ preventScroll: true });
  }
}

function showMessage(message, page = state.page) {
  $("message").textContent = message;
  $("message").dataset.page = page;
  $("message").hidden = page !== state.page;
}

const pageTabs = [...document.querySelectorAll("[data-page]")];
pageTabs.forEach((tab, index) => {
  tab.onclick = event => { event.preventDefault(); showPage(tab.dataset.page); };
  tab.onkeydown = event => {
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % pageTabs.length;
    else if (event.key === "ArrowLeft") next = (index + pageTabs.length - 1) % pageTabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = pageTabs.length - 1;
    else return;
    event.preventDefault(); showPage(pageTabs[next].dataset.page); pageTabs[next].focus();
  };
});
window.addEventListener("hashchange", () => showPage(location.hash.slice(1), { writeHistory: false }));
document.querySelector("[data-open-input]").onclick = () => showPage("input", { focus: true });
document.querySelector(".skip-link").onclick = event => { event.preventDefault(); showPage("input", { focus: true }); };

function node(tag, className, content) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (content !== undefined) el.textContent = content;
  return el;
}

function allWords() { return state.analysis?.sentences.flatMap(s => s.words) || []; }
function sourceElement(ids) {
  const box = node("div", "source"); box.append(document.createTextNode("來源："));
  ids.forEach((id, index) => {
    if (index) box.append(document.createTextNode("、"));
    const source = state.analysis.sources.find(s => s.id === id);
    const label = source ? `${source.name} · ${source.version}` : id;
    if (source?.locator && /^https?:\/\//.test(source.locator)) {
      const link = node("a", "", label); link.href = source.locator;
      link.target = "_blank"; link.rel = "noopener noreferrer"; box.append(link);
    } else box.append(document.createTextNode(label));
  });
  return box;
}
function fieldText(field) {
  if (!field || field.status !== "present") return field?.status === "unsupported" ? "目前未支援" : field?.status === "not_applicable" ? "不適用" : "此次無法判定";
  if (field.value && typeof field.value === "object") {
    if ("relation" in field.value) {
      const head = allWords().find(w => w.id === field.value.head_word_id);
      return field.value.head_word_id ? `${field.value.relation} → ${head?.form || field.value.head_word_id}` : "句子根節點";
    }
    return Object.entries(field.value).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : v}`).join(" · ") || "無標記";
  }
  return String(field.value);
}

function renderDetail() {
  const target = $("detail"); target.replaceChildren();
  const word = allWords().find(w => w.id === state.selected);
  $("word-source").disabled = !word;
  if (!word) { target.append(node("p", "subtle", "選取原文中的詞語，查看分析與來源。")); return; }
  target.append(node("h3", "", word.form));
  const segment = state.analysis.sentences.flatMap(s => s.segments).find(s => s.id === word.segment_id);
  if (segment.word_ids.length > 1) {
    target.append(node("p", "subtle", `原文片段「${segment.surface}」包含多個分析單位：`));
    const choices = node("div", "word-choices");
    for (const id of segment.word_ids) {
      const option = allWords().find(w => w.id === id);
      const button = node("button", id === state.selected ? "active" : "", option?.form || id);
      button.type = "button"; button.onclick = () => selectWord(id);
      button.setAttribute("aria-pressed", String(id === state.selected));
      choices.append(button);
    }
    target.append(choices);
  }
  const dl = node("dl", "");
  for (const [label, field] of [["Lemma", word.lemma], ["UPOS", word.upos], ["XPOS", word.xpos], ["Morphology", word.morphology], ["Dependency", word.dependency]]) {
    const row = node("div", "detail-row"); row.append(node("dt", "", label));
    const dd = node("dd", "");
    if (label === "Dependency" && field.status === "present" && field.value.head_word_id) {
      const head = allWords().find(w => w.id === field.value.head_word_id);
      const link = node("button", "head-link", `${field.value.relation} → ${head?.form || field.value.head_word_id}`);
      link.type = "button"; link.onclick = () => selectWord(field.value.head_word_id); dd.append(link);
    } else dd.append(node("span", "", fieldText(field)));
    if (field?.source_ids?.length) dd.append(sourceElement(field.source_ids));
    if (field?.certainty === "tentative") dd.append(node("div", "fallback", "待確認"));
    row.append(dd); dl.append(row);
  }
  target.append(dl);
  for (const [language, fields] of Object.entries(word.extensions || {})) {
    const details = node("details", "language-details");
    details.append(node("summary", "", language === "fr" ? "法文細節" : language === "ja" ? "日文細節" : language));
    const extended = node("dl", "");
    const labels = { reading: "讀音", normalized_form: "正規形", conjugation_type: "活用類型",
                     conjugation_form: "活用形", clitic_type: "Clitic 類型" };
    for (const [name, field] of Object.entries(fields)) {
      const row = node("div", "detail-row"); row.append(node("dt", "", labels[name] || name));
      const dd = node("dd", "", fieldText(field));
      if (field?.source_ids?.length) dd.append(sourceElement(field.source_ids));
      row.append(dd); extended.append(row);
    }
    details.append(extended); target.append(details);
  }
  const gloss = state.analysis.annotations.find(a => a.kind === "lexical_gloss" && a.word_ids[0] === word.id);
  if (gloss) {
    target.append(node("p", "gloss", `Gloss · ${fieldText(gloss.text)}`));
    target.append(sourceElement(gloss.text.source_ids));
  }
  const conflicts = state.analysis.disagreements.filter(d => d.word_id === word.id);
  if (conflicts.length) {
    target.append(node("h4", "", "分析分歧"));
    for (const d of conflicts) {
      const card = node("div", "disagreement fallback");
      card.append(node("strong", "", d.field));
      card.append(node("p", "", `${d.resolution === "unresolved" ? "原欄位未判定，不自動補入" : "保留 parser"}：${fieldText(d.selected)}`));
      if (d.selected.source_ids.length) card.append(sourceElement(d.selected.source_ids));
      for (const alternative of d.alternatives) {
        card.append(node("p", "", `替代分析（待確認）：${fieldText(alternative)}`));
        card.append(sourceElement(alternative.source_ids));
      }
      card.append(node("p", "", d.reason));
      target.append(card);
    }
  }
}

function selectWord(id) {
  state.selected = id;
  for (const button of document.querySelectorAll(".segment")) {
    const selected = button.dataset.words.split(",").includes(id);
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  }
  for (const row of $("syntax-rows").children) row.classList.toggle("selected", row.dataset.word === id);
  renderDetail();
  updateQuestionControls();
}

function setupMyTranslations(analysis, existing = null, enrichment = "off") {
  currentTranslations = new Map();
  const target = $("translations");
  const editors = new Map();
  const active = () => state.analysis === analysis;
  for (const [index, sentence] of analysis.sentences.entries()) {
    const box = node("section", "my-translation");
    const label = node("label", "", "句子 " + (index + 1) + " · 我的翻譯");
    const sentenceText = Array.from(analysis.input.text).slice(sentence.span.start, sentence.span.end).join("");
    const textarea = node("textarea"); textarea.rows = 2; textarea.maxLength = 4000;
    textarea.id = "my-translation-" + index; label.htmlFor = textarea.id;
    const button = node("button", "head-link", "保存我的翻譯"); button.type = "button";
    button.disabled = true;
    const status = node("span", "subtle", "正在讀取已保存的翻譯…");
    status.setAttribute("role", "status");
    const key = translationKey(sentence);
    const item = { textarea, button, status, dirty: false };
    editors.set(key, item);
    textarea.addEventListener("input", () => {
      item.dirty = true;
      status.textContent = "尚未保存；匯出目前分析時會使用欄位中的內容。";
    });
    button.onclick = async () => {
      const value = textarea.value;
      button.disabled = true; status.textContent = "正在保存…";
      try {
        await historyState.queue;
        let record = existing || historyState.saved.get(analysis.analysis_id);
        if (!record) {
          record = await historyRequest("", historyJSON("POST", { analysis, enrichment_requested: enrichment }));
          historyState.saved.set(analysis.analysis_id, record);
        }
        const saved = await historyRequest("/" + record.entry_id + "/translations", historyJSON("PUT", {
          snapshot_id: record.snapshot_id, sentence_id: sentence.id, text: value,
        }));
        if (!active()) return;
        if (saved.text) currentTranslations.set(key, saved.text); else currentTranslations.delete(key);
        item.dirty = textarea.value !== value;
        status.textContent = item.dirty ? "已保存先前內容；目前欄位有未保存的變更。" : "已保存。清空並保存可移除個人翻譯。";
        await loadHistory();
      } catch (error) {
        if (active()) status.textContent = "尚未保存：" + error.message;
      } finally {
        if (active()) button.disabled = false;
      }
    };
    box.append(node("p", "subtle", sentenceText), label, textarea, button, status); target.append(box);
  }
  (async () => {
    try {
      await historyState.queue;
      let record = existing || historyState.saved.get(analysis.analysis_id);
      // Read earlier translations without saving a new snapshot when auto-history is disabled.
      if (!record) {
        let cursor = null;
        do {
          const params = new URLSearchParams({ limit: "100", q: analysis.input.text, language: analysis.input.language });
          if (cursor) params.set("cursor", cursor);
          const page = await historyRequest("?" + params);
          record = page.items.find(item => item.original_text === analysis.input.text);
          cursor = record ? null : page.next_cursor;
        } while (cursor);
      }
      if (record) {
        const result = await historyRequest("/" + record.entry_id + "/translations");
        if (!active()) return;
        currentTranslations = translationMap(result.items);
      }
      if (!active()) return;
      for (const [key, item] of editors) {
        if (!item.dirty) item.textarea.value = currentTranslations.get(key) || "";
        item.button.disabled = false;
        item.status.textContent = item.dirty ? "尚未保存。" :
          record ? "可輸入個人翻譯；清空並保存可移除。" : "保存時會將這筆分析加入歷史紀錄。";
      }
    } catch (error) {
      if (active()) for (const item of editors.values()) {
        item.status.textContent = "無法讀取已保存的翻譯：" + error.message + "；請重新開啟紀錄。";
      }
    }
  })();
}

function currentTranslationDrafts() {
  const values = new Map(currentTranslations);
  state.analysis.sentences.forEach((sentence, index) => {
    const value = $("my-translation-" + index)?.value || "";
    if (value.trim()) values.set(translationKey(sentence), value); else values.delete(translationKey(sentence));
  });
  return values;
}

$("copy-analysis").onclick = async () => {
  if (!state.analysis) return;
  try {
    await navigator.clipboard.writeText(markdownDocument([{analysis: state.analysis, translations: currentTranslationDrafts()}], $("export-details").checked));
    $("export-status").textContent = "已複製筆記。";
  } catch { $("export-status").textContent = "無法存取剪貼簿，請使用下載 Markdown。"; }
};
$("export-analysis").onclick = () => {
  if (!state.analysis) return;
  downloadMarkdown(markdownDocument([{analysis: state.analysis, translations: currentTranslationDrafts()}], $("export-details").checked));
  $("export-status").textContent = "已下載筆記。";
};

function renderAnalysis(analysis, navigate = true) {
  resetQuestions();
  $("source-dialog").close();
  setAnalysisView("words");
  state.analysis = analysis; state.selected = null; $("result").hidden = false;
  $("analysis-original").textContent = analysis.input.text;
  $("analysis-original").lang = analysis.input.language;
  $("text").lang = analysis.input.language;
  $("analysis-language").textContent = "分析工作區 / " + ({ fr: "法文", ja: "日文", en: "英文" }[analysis.input.language] || analysis.input.language);
  $("analysis-count").textContent = `${analysis.sentences.length} 句 · ${allWords().filter(w => w.upos.value !== "PUNCT").length} 個詞語分析單位（不含標點）`;
  $("result-mode").textContent = analysis.status === "partial" ? "部分結果" : "分析完成";
  const warningBox = $("warnings"); warningBox.replaceChildren();
  for (const warning of analysis.warnings) {
    const card = node("div", "warning", warning.message);
    if (warning.diagnostic) {
      const details = node("details", "");
      details.append(node("summary", "", "查看原因與建議"));
      details.append(node("p", "", warning.diagnostic.detail));
      details.append(node("small", "", `診斷：${warning.diagnostic.reason} · ${warning.diagnostic.retryable ? "可重試" : "請先調整設定或輸入"}`));
      card.append(details);
    }
    warningBox.append(card);
  }
  if (analysis.sentences.some(sentence => sentence.dependency_status !== "complete")) {
    warningBox.append(node("div", "warning", "部分句子的依存資訊不完整，仍可查看其他詞語資訊。"));
  }
  const sentences = $("sentences"); sentences.replaceChildren();
  const translations = $("translations"); translations.replaceChildren();
  const constructions = $("constructions"); constructions.replaceChildren();
  const p = node("p", "sentence"); let at = 0;
  const allSegments = analysis.sentences.flatMap(sentence => sentence.segments);
  for (const segment of allSegments) {
    p.append(document.createTextNode(slice(analysis.input.text, at, segment.span.start)));
    const button = node("button", "segment"); button.type = "button";
    button.append(node("span", "segment-form", segment.surface));
    const words = segment.word_ids.map(id => allWords().find(w => w.id === id)).filter(Boolean);
    if (words.every(w => w.upos.value === "PUNCT")) button.classList.add("punctuation");
    else {
      const glosses = words.map(word => {
        const annotation = analysis.annotations.find(a => a.kind === "lexical_gloss" && a.word_ids.includes(word.id));
        return annotation ? fieldText(annotation.text) : "—";
      });
      button.append(node("span", "segment-gloss", glosses.join(" / ")));
      button.append(node("span", "segment-pos", words.map(w => w.upos.value || "未判定").join(" · ")));
    }
    button.dataset.words = segment.word_ids.join(","); button.setAttribute("aria-label", `分析 ${segment.surface}`);
    button.setAttribute("aria-pressed", "false");
    button.onclick = () => selectWord(segment.word_ids[0]); p.append(button); at = segment.span.end;
  }
  p.append(document.createTextNode(slice(analysis.input.text, at, codepoints(analysis.input.text).length)));
  sentences.append(p);
  for (const annotation of analysis.annotations) {
    if (annotation.kind === "lexical_gloss") continue;
    const card = node("div", annotation.kind === "translation" ? "translation" : "construction");
    card.append(node("strong", "", annotation.kind === "translation" ? "自然翻譯" : "構式說明"));
    card.append(node("span", "", fieldText(annotation.text)));
    if (annotation.text.source_ids?.length) card.append(sourceElement(annotation.text.source_ids));
    (annotation.kind === "translation" ? translations : constructions).append(card);
  }
  if (!translations.children.length) translations.append(node("p", "empty-note", "這份分析沒有翻譯。需要時可開啟模型補充後重新分析。"));
  if (!constructions.children.length) constructions.append(node("p", "empty-note", "這份分析沒有構式註解。可查看詞語的形態與句法欄位，或針對句子向模型追問；追問回答不會成為已驗證的語法說明。"));
  renderSyntax();
  renderDetail();
  updateQuestionControls();
  if (navigate) showPage("analysis", { focus: true });
  else syncPage();
}

function setAnalysisView(view) {
  for (const tab of document.querySelectorAll("[data-view]")) {
    const active = tab.dataset.view === view;
    tab.setAttribute("aria-selected", String(active)); tab.tabIndex = active ? 0 : -1;
    $("view-" + tab.dataset.view).hidden = !active;
  }
}

function renderSyntax() {
  const rows = $("syntax-rows"); rows.replaceChildren();
  const words = allWords();
  const wordButton = word => {
    const button = node("button", "", word.form); button.type = "button";
    button.onclick = () => selectWord(word.id); return button;
  };
  for (const word of words) {
    const row = node("tr", ""); row.dataset.word = word.id;
    const form = node("td", ""); form.append(wordButton(word));
    const dependency = word.dependency.status === "present" ? word.dependency.value : null;
    const relation = node("td", "", dependency ? dependency.relation : fieldText(word.dependency));
    const head = node("td", "");
    const target = dependency && words.find(w => w.id === dependency.head_word_id);
    if (target) head.append(wordButton(target));
    else head.textContent = dependency && dependency.head_word_id === null ? "—" : "未判定";
    row.append(form, relation, head); rows.append(row);
  }
}

function openSources(wordOnly = false) {
  if (!state.analysis) return;
  const word = wordOnly && allWords().find(w => w.id === state.selected);
  if (wordOnly && !word) return;
  const ids = new Set();
  const collect = value => {
    if (!value || typeof value !== "object") return;
    if (Array.isArray(value.source_ids)) value.source_ids.forEach(id => ids.add(id));
    Object.values(value).forEach(v => { if (v && typeof v === "object") collect(v); });
  };
  if (word) {
    collect(word);
    collect(state.analysis.annotations.filter(a => a.word_ids.includes(word.id)));
    collect(state.analysis.disagreements.filter(d => d.word_id === word.id));
  } else state.analysis.sources.forEach(source => ids.add(source.id));
  $("source-context").textContent = word ? `選取詞語：${word.form} · 詞語欄位與相關註解的來源` : "目前分析所記錄的來源；未提供的欄位不會補上示意資料。";
  const evidence = $("source-evidence"); evidence.replaceChildren();
  const kinds = { parser: "Parser", llm: "模型補充 · 未經人工驗證", rule: "規則", resource: "語言資源" };
  for (const source of state.analysis.sources.filter(source => ids.has(source.id))) {
    const block = node("section", "evidence");
    block.append(node("h3", "", kinds[source.kind] || source.kind));
    block.append(sourceElement([source.id]));
    evidence.append(block);
  }
  if (!evidence.children.length) evidence.append(node("p", "subtle", "目前欄位未提供來源。"));
  $("source-dialog").showModal();
}

const viewTabs = [...document.querySelectorAll("[data-view]")];
viewTabs.forEach((tab, index) => {
  tab.onclick = () => setAnalysisView(tab.dataset.view);
  tab.onkeydown = event => {
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % viewTabs.length;
    else if (event.key === "ArrowLeft") next = (index + viewTabs.length - 1) % viewTabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = viewTabs.length - 1;
    else return;
    event.preventDefault(); setAnalysisView(viewTabs[next].dataset.view); viewTabs[next].focus();
  };
});
$("analysis-source").onclick = () => openSources();
$("word-source").onclick = () => openSources(true);
$("close-source").onclick = () => $("source-dialog").close();
$("source-dialog").onclick = event => {
  const box = $("source-dialog").getBoundingClientRect();
  if (event.target === $("source-dialog") && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)) $("source-dialog").close();
};
$("edit-original").onclick = () => showPage("input", { focus: true });

// Reject on timeout even if a late response arrives after abort (sequence also guards rendering).
function fetchJSON(url, options, controller, timeout, timeoutMessage) {
  return new Promise((resolve, reject) => {
    let finished = false;
    let timer;
    const complete = (callback, value) => {
      if (finished) return;
      finished = true; clearTimeout(timer);
      controller.signal.removeEventListener("abort", onAbort);
      callback(value);
    };
    const onAbort = () => complete(reject, new DOMException("Cancelled", "AbortError"));
    controller.signal.addEventListener("abort", onAbort, { once: true });
    timer = setTimeout(() => {
      complete(reject, new Error(timeoutMessage));
      controller.abort();
    }, timeout);
    fetch(url, { ...options, signal: controller.signal }).then(async response => {
      let data;
      try { data = await response.json(); }
      catch { throw new Error("服務回應格式異常，請稍後重試。"); }
      if (!response.ok) throw new Error([data.error?.message || "分析服務暫時無法使用。", data.error?.detail].filter(Boolean).join(" "));
      return data;
    }).then(data => complete(resolve, data), error => {
      complete(reject, error instanceof TypeError ? new Error("無法連接分析服務，請確認服務已啟動。") : error);
    });
  });
}

function updateControls() {
  syncPage();
  const ready = state.capabilities?.languages.some(item => item.language === $("language").value && item.ready);
  $("submit").disabled = state.pending || questionState.pending || historyState.clearing || state.checking || !ready || window.location.protocol === "file:";
  $("refresh-models").disabled = state.pending || state.checking || window.location.protocol === "file:";
  updateQuestionControls();
  historyControls();
}

async function loadCapabilities() {
  state.checking = true;
  updateControls();
  if (window.location.protocol === "file:") {
    $("local-file-notice").hidden = false;
    $("readiness").textContent = "尚未連接本機服務";
    state.checking = false;
    updateControls();
    return;
  }
  try {
    const initial = state.capabilities === null;
    const previousLanguage = $("language").value;
    const capabilities = await fetchJSON("/api/v0.1/capabilities", {}, new AbortController(), 8000,
                                        "檢查模型逾時，請重新檢查。");
    if (!Array.isArray(capabilities.languages)) throw new Error("無效的模型資訊");
    state.capabilities = capabilities;
    for (const option of $("language").options) {
      const entry = state.capabilities.languages.find(x => x.language === option.value);
      option.disabled = !entry?.ready;
    }
    const first = [...$("language").options].find(option => !option.disabled);
    if (first && ![...$("language").options].some(option => option.value === previousLanguage && !option.disabled)) {
      $("language").value = first.value;
      if (initial && $("text").value === "J'en ai acheté trois.") {
        if (first.value === "ja") $("text").value = "昨日、本を三冊買った。";
        if (first.value === "en") $("text").value = "She gave him a book.";
      }
      invalidateCurrentRequest();
    }
    $("readiness").textContent = first ? "選取可用語言開始" : "模型尚未準備好，請確認安裝或稍後重新檢查。";
    updateEnrichment();
  } catch (error) {
    state.capabilities = null;
    $("readiness").textContent = error.message;
    updateEnrichment();
  } finally {
    state.checking = false;
    updateControls();
  }
}

function updateEnrichment() {
  const canEnrich = $("language").value !== "en" && !!state.capabilities?.enrichment_available;
  $("enrichment").disabled = !canEnrich;
  if (!canEnrich) $("enrichment").checked = false;
  const provider = state.capabilities?.enrichment_provider;
  $("enrichment-provider").textContent = $("language").value === "en" ? "英文僅提供基礎分析。" :
    canEnrich ? `${provider === "lmstudio" ? "LM Studio" : "OpenAI"} · ${state.capabilities.enrichment_model || "已設定模型"}（連線狀態待分析時確認）` :
      "模型補充尚未設定或設定無效；仍可使用基礎分析。";
}

function analysisTimeout(enrichment) {
  const timeout = state.capabilities?.analysis_timeout_ms;
  return enrichment === "on" && Number.isFinite(timeout) && timeout >= 15000 && timeout <= 315000
    ? timeout : ANALYSIS_TIMEOUT_MS;
}

function invalidateCurrentRequest() {
  resetHistoryVersions();
  resetQuestions();
  state.sequence += 1;
  state.controller?.abort();
  state.pending = false;
  state.analysis = null;
  state.selected = null;
  $("result").hidden = true;
  updateControls();
  showMessage("輸入已變更，可重新分析。", "input");
}
$("language").addEventListener("change", () => { updateEnrichment(); invalidateCurrentRequest(); });
$("text").addEventListener("input", invalidateCurrentRequest);
$("enrichment").addEventListener("change", invalidateCurrentRequest);
$("refresh-models").addEventListener("click", loadCapabilities);
$("sentences").addEventListener("keydown", (event) => {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  const buttons = [...$("sentences").querySelectorAll(".segment")];
  const at = buttons.indexOf(document.activeElement);
  if (at < 0) return;
  event.preventDefault();
  const next = buttons[(at + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length];
  next.focus(); selectWord(next.dataset.words.split(",")[0]);
});
$("analysis-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if ($("submit").disabled) return;
  if (!$("text").value.trim() || codepoints($("text").value).length > 500) {
    showMessage("請輸入 1–500 個字元，至少包含一個非空白字元。");
    return;
  }
  state.controller?.abort();
  const controller = new AbortController(); state.controller = controller;
  const sequence = ++state.sequence;
  const pageSequence = state.pageSequence;
  const payload = { text: $("text").value, language: $("language").value, enrichment: $("enrichment").checked ? "on" : "off" };
  state.analysis = null; state.pending = true; updateControls();
  resetHistoryVersions();
  resetQuestions();
  showMessage("正在分析…");
  try {
    const data = await fetchJSON("/api/v0.1/analyses", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    }, controller, analysisTimeout(payload.enrichment), "等待分析逾時。輸入已保留，可以重新分析。");
    if (sequence !== state.sequence) return;
    renderAnalysis(data, state.pageSequence === pageSequence); saveHistory(data, payload.enrichment);
    setupMyTranslations(data, null, payload.enrichment);
    showMessage(data.status === "partial" ? "部分分析已完成，請查看提示。" : "分析完成。", "analysis");
  } catch (error) {
    if (sequence === state.sequence && error.name !== "AbortError") {
      showMessage(error.message, "input");
    }
  } finally {
    if (sequence === state.sequence) { state.pending = false; updateControls(); }
  }
});
showPage(location.hash.slice(1), { writeHistory: false });
initHistory();
initQuestions();
loadCapabilities();
