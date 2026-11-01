# 심사역 콘솔. 빌드 컨텍스트는 저장소 루트입니다: az acr build -f src/console/Dockerfile .
# journey는 models·validation만 복사합니다(둘 다 pydantic만 쓰고, 오케스트레이터 런타임은 필요 없음).
FROM mcr.microsoft.com/azurelinux/base/python:3.12
RUN python3 -m venv /opt/venv
COPY requirements-console.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt
COPY src/shared /app/shared
COPY src/agents /app/agents
COPY src/orchestrator/journey/__init__.py src/orchestrator/journey/models.py src/orchestrator/journey/validation.py /app/journey/
COPY src/console /app/console
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
WORKDIR /app
USER 65532
EXPOSE 8000
CMD ["/opt/venv/bin/uvicorn", "console.main:app", "--host", "0.0.0.0", "--port", "8000"]
