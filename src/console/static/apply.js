// 동의 3종이 모두 체크될 때만 신청 버튼을 켠다. 서버도 같은 검증을 한다.
(function () {
  var form = document.getElementById("apply-form");
  if (!form) return;
  var boxes = form.querySelectorAll("input.consent");
  var button = document.getElementById("apply-submit");
  function sync() {
    var all = true;
    boxes.forEach(function (b) { if (!b.checked) all = false; });
    button.disabled = !all;
  }
  boxes.forEach(function (b) { b.addEventListener("change", sync); });
  sync();
})();