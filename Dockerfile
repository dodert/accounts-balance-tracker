FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    PORT=80 \
    PUID=99 \
    PGID=100 \
    UMASK=022 \
    TZ=Etc/UTC \
    FORWARDED_ALLOW_IPS=127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
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
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port \"$PORT\" --proxy-headers"]
