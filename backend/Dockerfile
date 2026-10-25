# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /src
COPY pyproject.toml ./
COPY repolens ./repolens
RUN pip install --default-timeout=120 --retries=6 --prefix=/install .

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 REPOLENS_REPOS_DIR=/data/repos
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home --home-dir /home/app app \
    && mkdir -p /data/repos && chown -R app /data \
    && git config --system --add safe.directory '*'
COPY --from=build /install /usr/local
WORKDIR /app
COPY repolens ./repolens
COPY alembic ./alembic
COPY alembic.ini ./
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
ENTRYPOINT ["tini", "--"]
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn repolens.main:app --host 0.0.0.0 --port 8000 --timeout-keep-alive 10 --limit-concurrency 200 --h11-max-incomplete-event-size 65536"]
