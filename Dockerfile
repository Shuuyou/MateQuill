FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install --with-deps chromium && apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
COPY app ./app
COPY web ./web
COPY docs ./docs
COPY prompts ./prompts
COPY data ./data
COPY scripts/fetch_official_assets.py ./scripts/fetch_official_assets.py
RUN python scripts/fetch_official_assets.py --per-product 12
ENV ALLOW_LOCAL_SETTINGS=0 PYTHONUNBUFFERED=1
EXPOSE 7860
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "7860", "--no-proxy-headers"]
