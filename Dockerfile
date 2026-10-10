FROM node:22-alpine AS frontend
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci
COPY frontend/ ./
ARG VITE_MAP_STYLE_URL=https://tiles.openfreemap.org/styles/positron
ENV VITE_MAP_STYLE_URL=$VITE_MAP_STYLE_URL
RUN npm run build

FROM python:3.12-slim AS production
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FRONTEND_DIST_PATH=/app/frontend-dist PORT=10000 WEB_CONCURRENCY=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system app && adduser --system --ingroup app app \
    && mkdir /app/data && chown app:app /app/data
COPY backend/pyproject.toml backend/constraints.txt ./
COPY --chown=app:app backend/app ./app
COPY --chown=app:app backend/alembic ./alembic
COPY --chown=app:app backend/alembic.ini ./alembic.ini
RUN pip install --no-cache-dir -c constraints.txt ".[inference]"
COPY --from=frontend --chown=app:app /web/dist /app/frontend-dist
USER app
EXPOSE 10000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','10000')+'/api/v1/health/ready')"
CMD ["python", "-m", "app.deployment"]
