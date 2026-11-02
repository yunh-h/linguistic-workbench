// Only paired, completed turns live in memory. No chat text is persisted.
const questionState = { turns: [], pending: false, sequence: 0, controller: null };

function updateQuestionControls() {
  const available = !!state.capabilities?.question_available;
  $("question-submit").disabled = !state.analysis || state.pending || state.checking || questionState.pending || !available;
  const word = allWords().find(w => w.id === state.selected);
  $("question-context").textContent = !available ? "模型服務尚未設定，仍可閱讀分析與歷史紀錄。" :
    `${state.capabilities.enrichment_provider === "lmstudio" ? "LM Studio" : "OpenAI"} · ${state.capabilities.enrichment_model} · ${word ? `針對「${word.form}」` : "針對目前句子"}`;
}

function resetQuestions() {
  questionState.sequence++;
  questionState.controller?.abort();
  questionState.pending = false;
  questionState.turns = [];
  $("question-messages").replaceChildren();
  $("question-input").value = "";
  $("question-status").textContent = "";
}

function renderQuestions() {
  const box = $("question-messages"); box.replaceChildren();
  for (const turn of questionState.turns) {
    const card = node("div", `chat-turn ${turn.role}`);
    card.append(node("strong", "", turn.role === "user" ? "你" : "模型 · 待確認"));
    card.append(node("p", "", turn.content)); box.append(card);
  }
}

function initQuestions() {
  $("question-clear").onclick = () => {
    const pending = questionState.pending;
    resetQuestions(); updateControls();
    $("question-status").textContent = pending ? "已停止等待並清除對話；模型服務可能仍在生成。" : "對話已清除。";
  };
  $("question-form").onsubmit = async event => {
    event.preventDefault();
    if ($("question-submit").disabled) return;
    const question = $("question-input").value.trim();
    if (!question || codepoints(question).length > 1000) {
      $("question-status").textContent = "請輸入 1–1000 字的問題。"; return;
    }
    const controller = new AbortController(); questionState.controller = controller;
    const sequence = ++questionState.sequence;
    const payload = { analysis: state.analysis, selected_word_id: state.selected, question, history: questionState.turns };
    questionState.pending = true; updateControls();
    $("question-status").textContent = "正在詢問模型…";
    const advertised = state.capabilities?.question_timeout_ms;
    const timeout = Number.isFinite(advertised) && advertised >= 10000 && advertised <= 305000 ? advertised : 95000;
    try {
      const data = await fetchJSON("/api/v0.1/questions", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      }, controller, timeout, "等待模型回答逾時，問題已保留，可稍後重試。");
      if (sequence !== questionState.sequence) return;
      if (typeof data.answer !== "string" || !data.answer.trim()) throw new Error("模型沒有回傳可讀的回答。");
      questionState.turns.push({ role: "user", content: question }, { role: "assistant", content: data.answer });
      questionState.turns = questionState.turns.slice(-6);
      renderQuestions(); $("question-input").value = "";
      $("question-status").textContent = `${data.model} · 回答未經語言學驗證，不會修改原分析。僅保留最近三輪。`;
    } catch (error) {
      if (sequence === questionState.sequence && error.name !== "AbortError") $("question-status").textContent = error.message;
    } finally {
      if (sequence === questionState.sequence) { questionState.pending = false; updateControls(); }
    }
  };
}
