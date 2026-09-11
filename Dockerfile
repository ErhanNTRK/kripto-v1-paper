FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1
COPY crypto_v1 ./crypto_v1
COPY config.json ./config.json
CMD ["python", "-m", "crypto_v1.cloud"]
