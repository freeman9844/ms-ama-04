"""신청 상태 라벨과 종결 상태. app(라우트)과 dashboard(집계)가 함께 쓴다."""

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
