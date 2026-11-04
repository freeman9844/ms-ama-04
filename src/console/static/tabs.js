// 신청 상세 탭(P9 §11). JS가 없으면 모든 탭 패널이 펼쳐진 채로 보이고 탭 목록은 앵커 링크로 동작한다.
(function () {
  "use strict";
  var root = document.querySelector("[data-tabs]");
  if (!root) return;
  var tabs = Array.prototype.slice.call(root.querySelectorAll('[role="tab"]'));
  var panels = tabs.map(function (t) { return document.getElementById(t.getAttribute("aria-controls")); });

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
    if (show(id, focus) && history.replaceState) history.replaceState(null, "", "#" + id);
  }

  root.classList.add("tabs-on");
  if (!show(location.hash.slice(1))) show(root.getAttribute("data-default-tab"));

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
  window.addEventListener("hashchange", function () { show(location.hash.slice(1)); });
})();