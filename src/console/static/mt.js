// 영문 화면의 기계 번역. 서버가 data-mt(가명 문구)로 표시한 데이터 문구를 /i18n/translate로 한 번에 옮기고,
// 응답의 토큰은 페이지의 script.mt-tokens 지도로 화면 값에 되돌린다.
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

  // 페이지(HTMX로 바뀐 조각 포함)의 모든 토큰 지도를 합친다. 깨진 JSON은 건너뛴다.
  const tokenMap = () => {
    const map = new Map();
    document.querySelectorAll("script.mt-tokens").forEach((script) => {
      try {
        const data = JSON.parse(script.textContent);
        if (!data || typeof data !== "object" || Array.isArray(data)) return;
        for (const [token, value] of Object.entries(data)) {
          if (typeof value === "string") map.set(token, value);
        }
      } catch {
        // 이 지도는 쓰지 않는다
      }
    });
    return map;
  };

  // 지도에 있는 토큰만 화면 값으로 되돌린다. 없는 토큰(감사자 화면 등)은 그대로 둔다.
  const restore = (text, map) => text.replace(TOKEN, (token) => (map.has(token) ? map.get(token) : token));

  const apply = (nodes, translation) => {
    const text = restore(translation, tokenMap());
    for (const node of nodes) {
      const original = node.textContent;
      node.removeAttribute("data-mt");
      node.textContent = text;
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
    document.querySelectorAll("[data-mt-note]").forEach((n) => { n.hidden = false; });
    for (const batch of chunks([...pending.keys()])) {
      try {
        const response = await fetch("/i18n/translate", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
          body: JSON.stringify({ texts: batch }),
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
