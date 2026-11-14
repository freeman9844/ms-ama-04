// 심사역 결정 채팅. 대화 기록은 이 페이지의 메모리에만 둔다(서버·브라우저 저장소에 남기지 않음).
(() => {
  const MAX_MESSAGES = 12; // 최근 6턴
  const MAX_TEXT = 2000;
  const TEXT = {
    ko: { busy: "답변 작성 중…", send: "보내기", failed: "답변을 받지 못했습니다." },
    en: { busy: "Writing answer…", send: "Send", failed: "Could not get an answer." },
  }[document.documentElement.lang === "en" ? "en" : "ko"];
  const form = document.querySelector("form[data-officer-chat]");
  if (!form) return;
  const log = document.querySelector("[data-chat-log]");
  const input = form.querySelector('textarea[name="question"]');
  const button = form.querySelector('button[type="submit"]');
  const suggestions = document.querySelectorAll("[data-chat-suggest]");
  const history = [];

  const append = (role, text) => {
    const item = document.createElement("div");
    item.className = `chat-msg chat-${role}`;
    item.textContent = text;
    log.appendChild(item);
    log.scrollTop = log.scrollHeight;
  };

  const setBusy = (busy) => {
    input.disabled = busy;
    button.disabled = busy;
    suggestions.forEach((b) => { b.disabled = busy; });
    button.textContent = busy ? TEXT.busy : TEXT.send;
  };

  const ask = async (raw) => {
    const question = raw.trim();
    if (!question || button.disabled) return;
    append("user", question);
    setBusy(true);
    try {
      const response = await fetch(form.dataset.url, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": form.dataset.csrf },
        body: JSON.stringify({ question, history: history.slice(-MAX_MESSAGES) }),
      });
      const body = await response.json().catch(() => ({}));
      if (response.ok && typeof body.answer === "string") {
        append("assistant", body.answer);
        history.push({ role: "user", text: question.slice(0, MAX_TEXT) },
                     { role: "assistant", text: body.answer.slice(0, MAX_TEXT) });
        input.value = "";
      } else {
        append("system", body.error || TEXT.failed);
      }
    } catch {
      append("system", TEXT.failed);
    } finally {
      setBusy(false);
      input.focus();
    }
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    ask(input.value);
  });
  suggestions.forEach((b) => b.addEventListener("click", () => ask(b.textContent)));
})();
