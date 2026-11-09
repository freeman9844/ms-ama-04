// 신청 상세 탭(P9 §11). JS가 없으면 모든 탭 패널이 펼쳐진 채로 보이고 탭 목록은 앵커 링크로 동작한다.
// 고른 탭은 신청별로 sessionStorage에 남겨 자동 새로 고침·결정 후 이동(해시가 사라짐)에도 유지한다.
// 결정 오류로 다시 그린 화면(data-tab-reset)은 오류가 보이도록 서버가 정한 탭을 연다.
(function () {
  "use strict";
  var root = document.querySelector("[data-tabs]");
  if (!root) return;
  var tabs = Array.prototype.slice.call(root.querySelectorAll('[role="tab"]'));
  var panels = tabs.map(function (t) { return document.getElementById(t.getAttribute("aria-controls")); });
  var key = "tab:" + location.pathname;

  function remember(id) {
    try { sessionStorage.setItem(key, id); } catch (e) { /* 저장소를 쓸 수 없으면 해시만 쓴다 */ }
  }

  function recalled() {
    try { return sessionStorage.getItem(key) || ""; } catch (e) { return ""; }
  }

  function show(id, focus) {
    if (!tabs.some(function (t) { return t.getAttribute("aria-controls") === id; })) return false;
    tabs.forEach(function (t, i) {
      var on = t.getAttribute("aria-controls") === id;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      if (panels[i]) panels[i].hidden = !on;
      if (on && focus) t.focus();
    });
    return true;
  }

  function select(id, focus) {
    if (!show(id, focus)) return;
    remember(id);
    if (history.replaceState) history.replaceState(null, "", "#" + id);
  }

  root.classList.add("tabs-on");
  var fallback = root.getAttribute("data-default-tab");
  if (root.hasAttribute("data-tab-reset")) {
    select(fallback);
  } else if (show(location.hash.slice(1))) {
    remember(location.hash.slice(1));
  } else if (!show(recalled())) {
    show(fallback);
  }

  tabs.forEach(function (t, i) {
    t.addEventListener("click", function (e) {
      e.preventDefault();
      select(t.getAttribute("aria-controls"));
    });
    t.addEventListener("keydown", function (e) {
      var step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!step) return;
      e.preventDefault();
      select(tabs[(i + step + tabs.length) % tabs.length].getAttribute("aria-controls"), true);
    });
  });
  // 근거 거래 칩처럼 본문 안의 #tab-… 링크도 탭을 연다.
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest('a[href^="#tab-"]');
    if (!a || a.getAttribute("role") === "tab") return;
    e.preventDefault();
    select(a.getAttribute("href").slice(1));
  });
  window.addEventListener("hashchange", function () { if (show(location.hash.slice(1))) remember(location.hash.slice(1)); });
})();