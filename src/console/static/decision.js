// 심사역 결정 폼(신청 결정·재조정 결정) 제출 처리. 서버도 같은 검증을 한다.
// - 정책 결과상 거절(또는 결과 없음)인 건을 승인할 때 확인을 받고 예외 승인 표시를 붙인다(policy_override=1과 사유 필수).
// - 제출이 진행되면 버튼을 꺼 중복 전송을 막는다.
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-decision-form")) return;
  if (form.hasAttribute("data-policy-blocked")) {
    const flag = form.querySelector('input[name="policy_override"]');
    const choice = form.querySelector('input[name="decision"]:checked');
    flag.value = "";
    if (choice && choice.value !== "decline") {
      if (!window.confirm("정책 결과상 거절이지만 승인하겠습니까?")) {
        event.preventDefault();
        return;
      }
      flag.value = "1";
    }
  }
  const button = form.querySelector('button[type="submit"]');
  if (button) {
    button.disabled = true;
    button.textContent = "보내는 중…";
  }
});
