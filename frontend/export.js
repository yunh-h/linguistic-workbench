// Markdown is an interchange format: escape untrusted text, including raw HTML.
function md(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/\\/g, "\\\\").replace(/\|/g, "\\|").replace(/\r?\n/g, "<br>");
}

function translationKey(sentence) { return sentence.span.start + ":" + sentence.span.end; }
function translationMap(items) {
  return new Map(items.map(item => [item.span_start + ":" + item.span_end, item.text]));
}

function markdownRecord(analysis, translations, details) {
  const language = { fr: "法文", ja: "日文", en: "英文" }[analysis.input.language] || analysis.input.language;
  const lines = ["## " + md(language) + "分析", "", "### 原文", "", md(analysis.input.text), ""];
  const wordsById = new Map(analysis.sentences.flatMap(s => s.words).map(w => [w.id, w]));
  for (const [index, sentence] of analysis.sentences.entries()) {
    const sourceText = Array.from(analysis.input.text).slice(sentence.span.start, sentence.span.end).join("");
    lines.push("### 句子 " + (index + 1), "", "**原文：** " + md(sourceText), "",
      "| 原文片段 | 分析單位 | Lemma | 詞性 | Gloss | 讀音 |",
      "| --- | --- | --- | --- | --- | --- |");
    for (const segment of sentence.segments) {
      for (const id of segment.word_ids) {
        const word = wordsById.get(id);
        if (!word) continue;
        const gloss = analysis.annotations.find(a => a.kind === "lexical_gloss" && a.word_ids.includes(id));
        const reading = word.extensions?.ja?.reading;
        lines.push("| " + [segment.surface, word.form, word.lemma.value ?? "—", word.upos.value ?? "—",
          gloss?.text?.value ?? "—", reading?.value ?? "—"].map(md).join(" | ") + " |");
      }
    }
    const own = translations.get(translationKey(sentence));
    const model = analysis.annotations.find(a => a.kind === "translation" && a.sentence_id === sentence.id &&
      a.text.status === "present");
    lines.push("", own?.trim() ? "**翻譯（使用者）：** " + md(own) :
      model ? "**翻譯（模型）：** " + md(model.text.value) : "**翻譯：** 尚無翻譯", "");
    for (const a of analysis.annotations.filter(a => a.kind === "construction" &&
      a.sentence_id === sentence.id && a.text.status === "present")) {
      const names = a.text.source_ids.map(id => analysis.sources.find(s => s.id === id)?.name || id);
      lines.push("**語法提示：** " + md(a.text.value) + "（來源：" + names.map(md).join("、") + "）", "");
    }
    if (details) {
      lines.push("#### 詳細語法分析", "", "| 分析單位 | 形態 | 依存關係 |", "| --- | --- | --- |");
      for (const word of sentence.words) {
        const morph = word.morphology.value ? Object.entries(word.morphology.value)
          .map(([key, values]) => key + "=" + values.join(",")).join("; ") : "—";
        const dep = word.dependency.value;
        const relation = dep ? dep.relation + (dep.head_word_id ? " → " +
          (wordsById.get(dep.head_word_id)?.form || dep.head_word_id) : "（根節點）") : "—";
        lines.push("| " + [word.form, morph || "—", relation].map(md).join(" | ") + " |");
      }
      lines.push("");
    }
  }
  if (analysis.sources.length) {
    lines.push("### 來源", "");
    for (const source of analysis.sources) lines.push("- " + md(source.kind) + "：" + md(source.name) + " · " + md(source.version));
    if (analysis.sources.some(source => source.kind === "llm")) lines.push("- 模型補充未經人工驗證。");
    lines.push("");
  }
  if (analysis.status === "partial" || analysis.warnings.length || analysis.disagreements.length) {
    lines.push("### 提醒", "");
    if (analysis.status === "partial") lines.push("- 這份分析為部分結果。");
    for (const warning of analysis.warnings) lines.push("- " + md(warning.message));
    for (const d of analysis.disagreements) {
      const word = wordsById.get(d.word_id);
      const selected = typeof d.selected.value === "object" ? JSON.stringify(d.selected.value) : d.selected.value;
      const alternatives = d.alternatives.map(a => typeof a.value === "object" ? JSON.stringify(a.value) : a.value);
      lines.push("- 分析分歧：" + md(word?.form || d.word_id) + " · " + md(d.field) +
        "；保留：" + md(selected ?? "未判定") + "；替代：" + alternatives.map(md).join("、") + "；" + md(d.reason));
    }
    lines.push("");
  }
  return lines.join("\n");
}

function markdownDocument(records, details) {
  return "# Linguistic Workbench 筆記\n\n" + records.map(r => markdownRecord(r.analysis, r.translations, details)).join("\n---\n\n");
}

function downloadMarkdown(content) {
  const url = URL.createObjectURL(new Blob([content], { type: "text/markdown;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url; link.download = "linguistic-workbench.md";
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
