# Pinned by digest (the multi-arch index), so a build of the same commit always starts from the same image;
# Dependabot updates it
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    PORT=80 \
    PUID=99 \
    PGID=100 \
    UMASK=022 \
    TZ=UTC \
    FORWARDED_ALLOW_IPS=127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7

WORKDIR /app
COPY requirements.txt .
# Exact versions with their hashes (requirements.txt is made from requirements.in by pip-compile)
RUN pip install --require-hashes --no-deps -r requirements.txt

COPY app ./app
# Read by the app to show what changed in each version (#205)
COPY CHANGELOG.md ./CHANGELOG.md
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ARG VERSION=dev
ARG GIT_SHA=""
ARG GIT_REF=""
ARG BUILD_DATE=""
ENV APP_VERSION=${VERSION} \
    GIT_SHA=${GIT_SHA} \
    GIT_REF=${GIT_REF} \
    BUILD_DATE=${BUILD_DATE}
LABEL org.opencontainers.image.title="Accounts Balance Tracker" \
      org.opencontainers.image.description="Self-hosted tracker of your accounts balances over time" \
      org.opencontainers.image.source="https://github.com/dodert/accounts-balance-tracker" \
      org.opencontainers.image.version="${VERSION}"

VOLUME /data
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"80\")}/api/health', timeout=4)"

ENTRYPOINT ["/entrypoint.sh"]
# One process only (--workers 1, whatever WEB_CONCURRENCY says): the login limits, the demos, the per-user
# database locks live in this process's memory; more processes would each have their own
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port \"$PORT\" --proxy-headers --workers 1"]
