# 평가 하니스 Job. 빌드 컨텍스트는 저장소 루트입니다: az acr build -f src/evals/Dockerfile .
# policy·mockapis는 엔진·합성기만 쓰므로 FastAPI 런타임은 필요 없습니다.
FROM mcr.microsoft.com/azurelinux/base/python:3.12
RUN python3 -m venv /opt/venv
COPY requirements-eval.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt
COPY src/shared /app/shared
COPY src/agents /app/agents
COPY src/policy /app/policy
COPY src/mockapis /app/mockapis
COPY src/evals /app/evals
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
WORKDIR /app
USER 65532
CMD ["/opt/venv/bin/python", "-m", "evals"]
