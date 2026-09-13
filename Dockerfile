FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1
COPY crypto_v1 ./crypto_v1
COPY config.json ./config.json
COPY live_config.json ./live_config.json
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
CMD ["python", "-m", "crypto_v1.render_web"]
