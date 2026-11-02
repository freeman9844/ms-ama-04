"""운영 화면(console /ops)과 보고서(ops metrics)가 함께 쓰는 라벨. 단계 용어는 콘솔 타임라인 상태 라벨과 같다."""

STEP_LABELS = {"consent": "동의", "assemble": "데이터 수집", "classify": "거래 분류", "underwrite": "심사 지표 산출",
               "policy": "정책 판정", "assess": "신용 평가"}
AXIS_LABELS = {"industry": "업종", "region": "지역", "business_age_band": "업력"}
