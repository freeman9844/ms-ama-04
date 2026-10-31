# Mock 마이데이터·국세청·PG API. 동의 레지스트리가 메모리에 있으므로 복제본 1개로 운영합니다.
# 빌드 컨텍스트는 저장소 루트입니다: az acr build -f src/mockapis/Dockerfile .
FROM mcr.microsoft.com/azurelinux/base/python:3.12
RUN python3 -m venv /opt/venv
COPY requirements-api.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt
COPY src/shared /app/shared
COPY src/mockapis /app/mockapis
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
WORKDIR /app
USER 65532
EXPOSE 8000
CMD ["/opt/venv/bin/uvicorn", "mockapis.main:app", "--host", "0.0.0.0", "--port", "8000"]
