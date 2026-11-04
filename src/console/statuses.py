"""신청 상태·플래그 라벨과 종결 상태. app(라우트)·dashboard(집계)·assessment(상세 패널)가 함께 쓴다."""

TERMINAL = frozenset({"approved", "conditionally_approved", "declined", "closed_no_decision", "withdrawn", "failed"})
STATUS_LABELS = {
    "received": "접수",
    "consent_granted": "동의 완료",
    "data_assembled": "데이터 수집",
    "data_incomplete": "데이터 부족",
    "underwritten": "심사 지표 산출",
    "assessed": "에이전트 평가",
    "awaiting_officer": "심사역 대기",
    "escalated": "SLA 초과",
    "approved": "승인",
    "conditionally_approved": "조건부 승인",
    "declined": "거절",
    "closed_no_decision": "미결 종료",
    "withdrawn": "철회 종료",
    "failed": "실패",
}
FLAG_LABELS = {
    "high_cost_lender_detected": "고금리 대부 거래 감지",
    "data_incomplete": "데이터 부족",
    "policy_unavailable": "정책 엔진 응답 없음",
    "assessment_unavailable": "에이전트 평가 없음",
    "review_not_passed": "검토 에이전트 미통과",
    "agent_fallback": "에이전트 대체 경로 사용",
    "agent_assessment_mismatch": "에이전트 권고와 정책 판정 불일치",
    "monitoring_unavailable": "사후 모니터링 시작 실패",
}
