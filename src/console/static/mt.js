// 영문 화면의 기계 번역. 서버가 data-mt(가명 문구)로 표시한 데이터 문구를 /i18n/translate로 한 번에 옮기고,
// 응답의 토큰은 각 문구에서 마스킹 전 보였던 값만 되돌린다.
// 실패하면 원문을 그대로 둔다. 번역문에는 mt-done 표시와 원문 툴팁을 단다(원문이 근거).
(() => {
  const MAX_ITEMS = 25;
  const MAX_CHARS = 8000;
  const csrf = document.body.dataset.mtCsrf;
  if (!csrf || document.documentElement.lang !== "en") return;

  const chunks = (texts) => {
    const out = [];
    let batch = [];
    let chars = 0;
    for (const t of texts) {
      if (batch.length && (batch.length >= MAX_ITEMS || chars + t.length > MAX_CHARS)) {
        out.push(batch);
        batch = [];
        chars = 0;
      }
      batch.push(t);
      chars += t.length;
    }
    if (batch.length) out.push(batch);
    return out;
  };

  const TOKEN = /\[\[[A-Z]+_[0-9a-f]{10}\]\]/g;

  const apply = (nodes, translation) => {
    for (const node of nodes) {
      const original = node.textContent;
      const visible = JSON.parse(node.dataset.mtRestore || "{}");
      node.textContent = translation.replace(TOKEN, token => visible[token] ?? token);
      node.removeAttribute("data-mt");
      node.removeAttribute("data-mt-restore");
      node.classList.add("mt-done");
      if (!node.classList.contains("mt-ref")) node.title = `Machine translation · original: ${original}`;
    }
  };

  const run = async (root) => {
    const pending = new Map();
    root.querySelectorAll("[data-mt]").forEach((node) => {
      if (node.dataset.mtBusy) return;
      node.dataset.mtBusy = "1";
      const text = node.dataset.mt;
      if (!pending.has(text)) pending.set(text, []);
      pending.get(text).push(node);
    });
    if (!pending.size) return;
    const scripts = root.querySelectorAll("script.mt-grant");
    if (scripts.length !== 1) return;
    let grant;
    try {
      grant = JSON.parse(scripts[0].textContent);
    } catch {
      return;
    }
    document.querySelectorAll("[data-mt-note]").forEach((n) => { n.hidden = false; });
    for (const batch of chunks([...pending.keys()])) {
      try {
        const response = await fetch("/i18n/translate", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
          body: JSON.stringify({ texts: batch, scope: grant.scope, tickets: batch.map(t => grant.tickets[t]) }),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok || !Array.isArray(body.translations)) continue;
        batch.forEach((text, i) => {
          if (typeof body.translations[i] === "string" && body.translations[i]) apply(pending.get(text), body.translations[i]);
        });
      } catch {
        // 원문 유지
      }
    }
  };

  if (document.querySelector(".mt-done")) {
    document.querySelectorAll("[data-mt-note]").forEach((n) => { n.hidden = false; });
  }
  run(document);
  document.body.addEventListener("htmx:afterSwap", (event) => run(event.target));
})();
