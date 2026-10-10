FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

RUN addgroup --system app && adduser --system --ingroup app app && mkdir /app/data && chown app:app /app/data
COPY pyproject.toml constraints.txt ./

FROM base AS development
COPY . .
RUN pip install --no-cache-dir -c constraints.txt -e ".[dev,ml]"
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM base AS training
COPY --chown=app:app . .
RUN pip install --no-cache-dir -c constraints.txt ".[ml]"
USER app
ENTRYPOINT ["python", "-m", "app.modules.ml.cli"]

FROM base AS production
COPY --chown=app:app . .
RUN pip install --no-cache-dir -c constraints.txt ".[inference]"
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

