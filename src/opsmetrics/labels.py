"""운영 화면(console /ops)과 보고서(ops metrics)가 함께 쓰는 라벨. 데이터 수집·심사 지표 산출은 콘솔 타임라인 상태 라벨과 같고, assess만 에이전트 단계 이름 '신용 평가'를 쓴다(타임라인 라벨은 '에이전트 평가')."""

STEP_LABELS = {"consent": "동의", "assemble": "데이터 수집", "classify": "거래 분류", "underwrite": "심사 지표 산출",
               "policy": "정책 판정", "assess": "신용 평가"}
AXIS_LABELS = {"industry": "업종", "region": "지역", "business_age_band": "업력"}
