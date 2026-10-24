FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY web-next/package*.json ./
RUN npm ci
COPY web-next ./
ENV NEXT_TELEMETRY_DISABLED=1 API_ORIGIN=http://127.0.0.1:8000 NODE_OPTIONS=--max-old-space-size=1024
RUN npm run build

FROM node:22-bookworm-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv fonts-noto-cjk ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
COPY constraints-runtime.txt ./
RUN python3 -m venv /app/.venv && /app/.venv/bin/pip install --no-cache-dir -r requirements.txt -c constraints-runtime.txt && /app/.venv/bin/python -m playwright install --with-deps chromium
COPY app ./app
COPY web ./web
COPY prompts ./prompts
COPY docs/PLAN.html ./docs/PLAN.html
COPY data ./data
COPY scripts/run_services.py ./scripts/run_services.py
COPY scripts/fetch_official_assets.py ./scripts/fetch_official_assets.py
COPY scripts/tenant_admin.py ./scripts/tenant_admin.py
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
RUN /app/.venv/bin/python scripts/fetch_official_assets.py
COPY --from=frontend /build/.next/standalone ./frontend
COPY --from=frontend /build/.next/static ./frontend/.next/static
COPY --from=frontend /build/public ./frontend/public
ENV ALLOW_LOCAL_SETTINGS=0 PYTHONUNBUFFERED=1 NEXT_TELEMETRY_DISABLED=1 PORT=7860 API_PORT=8000
ENV MATEQUILL_DEMO_ACCOUNTS=1 MATEQUILL_STUDIO_PUBLIC_ORIGIN=https://shuuyou-matequill.ms.show MATEQUILL_ALLOWED_ORIGINS=https://shuuyou-matequill.ms.show,https://studio-shuuyou-matequill.api-inference.modelscope.net MATEQUILL_COOKIE_SECURE=1 MATEQUILL_COOKIE_SAMESITE=none
EXPOSE 7860
CMD ["/app/.venv/bin/python", "scripts/run_services.py", "--frontend", "frontend"]
