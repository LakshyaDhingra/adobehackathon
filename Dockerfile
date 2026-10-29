FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    git libgl1 libegl1 libglib2.0-0 fontconfig \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY chameleon_design_schema_v0.9.json ./
COPY brand_kit/ ./brand_kit/
COPY renderer/ ./renderer/
COPY engine/ ./engine/
COPY ai/ ./ai/
RUN mkdir -p /app/input /app/output
ENTRYPOINT ["python", "-m", "engine.main"]
