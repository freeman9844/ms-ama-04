// 정책 결과상 거절(또는 결과 없음)인 건을 승인할 때 확인을 받고 예외 승인 표시를 붙인다.
// 서버도 policy_override=1과 사유가 없으면 거부한다.
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-policy-blocked")) return;
  const flag = form.querySelector('input[name="policy_override"]');
  const choice = form.querySelector('input[name="decision"]:checked');
  flag.value = "";
  if (!choice || choice.value === "decline") return;
  if (window.confirm("정책 결과상 거절이지만 승인하겠습니까?")) {
    flag.value = "1";
  } else {
    event.preventDefault();
  }
});
