# Agentic Cash-Flow Lending — Reference Implementation

Case Study 18 캡스톤 데모. 설계: `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md`

## 로컬 개발

사전 요구: Python 3.12 (`winget install --id Python.Python.3.12 -e --source winget`)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
```

로컬 서비스 실행:

```powershell
$env:MOCK_CONSENT_SECRET = "local-dev-secret-at-least-32-characters!!"
.\.venv\Scripts\python -m uvicorn mockapis.main:app --port 8001
.\.venv\Scripts\python -m uvicorn policy.main:app --port 8002
```

컨테이너 이미지는 로컬 Docker 없이 ACR Build로 만든다(P3).
