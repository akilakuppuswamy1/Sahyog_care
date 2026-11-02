# syntax=docker/dockerfile:1
FROM python:3.14-slim
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/pip pip install --retries 10 --timeout 120 torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install --retries 10 --timeout 120 -r requirements.txt
COPY . .
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
