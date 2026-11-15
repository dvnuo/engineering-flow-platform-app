# The EFP app host: one container that runs every app.
#
# Build from the repository root (the image needs host/, sdk/ and templates/):
#
#     docker build -f host/Dockerfile -t efp-app-host .
#
# Inside: tini -> supervisord -> nginx (the gateway, port 8080), the manager
# (port 9000) and one supervised process per app. See docs/OPERATIONS.md.

# bookworm on purpose: the gateway is written for its nginx 1.22 / njs 0.7.
ARG BASE_IMAGE=python:3.11-slim-bookworm

# ---- the SDK wheel that is bundled for apps -----------------------------------
FROM ${BASE_IMAGE} AS sdk
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
# setup.py of the SDK copies the starters from ../templates into the wheel.
COPY sdk/ /src/sdk/
COPY templates/ /src/templates/
RUN pip install --no-cache-dir build \
    && python -m build --wheel --outdir /wheels /src/sdk \
    && ls -l /wheels/efp_app_sdk-*.whl

# ---- the host ---------------------------------------------------------------------
FROM ${BASE_IMAGE}
ARG PIP_INDEX_URL=https://pypi.org/simple

LABEL org.opencontainers.image.title="efp-app-host" \
      org.opencontainers.image.description="Runs every EFP member app in one container: gateway, manager and process supervision."

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    EFP_HOST_RESOURCE_DIR=/opt/efp

# nginx with njs: the gateway.      bubblewrap: the sandbox backends.
# util-linux: setpriv.              tini: reaps what apps leave behind.
# supervisord itself comes from pip, as a dependency of the host package, so
# that the image has one Python only.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        nginx \
        libnginx-mod-http-js \
        bubblewrap \
        util-linux \
        tini \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

COPY host/pyproject.toml /opt/efp/src/pyproject.toml
COPY host/efp_app_host/ /opt/efp/src/efp_app_host/
RUN pip install --no-cache-dir /opt/efp/src \
    && rm -rf /opt/efp/src \
    && python3 -m compileall -q /usr/local/lib/python3.11/site-packages/efp_app_host

COPY host/nginx/ /opt/efp/nginx/
COPY host/pages/ /opt/efp/pages/
COPY host/bin/ /opt/efp/bin/
COPY host/supervisor/supervisord.conf /etc/supervisor/supervisord.conf
COPY --from=sdk /wheels/ /opt/efp/wheels/

# - the directory above the volume exists in the image, so that the
#   entrypoint can close it (hidden mode) or open it (sandbox mode);
# - no setuid or setgid program is left: an app must never gain privileges
#   by running one.
RUN chmod 0755 /opt/efp/bin/efp-app-launch /opt/efp/bin/entrypoint.sh \
    && mkdir -p /var/lib/efp-host/store /srv/efp-apps /run/efp-host /etc/supervisor \
    && chmod 0755 /var/lib/efp-host /var/lib/efp-host/store /srv/efp-apps /run/efp-host \
    && find / -xdev -type f \( -perm -4000 -o -perm -2000 \) -exec chmod a-s {} + \
    && test -z "$(find / -xdev -type f \( -perm -4000 -o -perm -2000 \) 2>/dev/null)"

# Fail the build, not the first start, when a part is missing or the gateway
# configuration does not load. The secret made for this check is removed.
RUN bwrap --version \
    && setpriv --version \
    && env --default-signal=TERM true \
    && tini --version \
    && nginx -v \
    && test -x /usr/local/bin/supervisord \
    && python3 -c "import efp_app_host, psutil, supervisor, uvicorn" \
    && ls /opt/efp/wheels/efp_app_sdk-*.whl \
    && mkdir -p /tmp/efp-build-check \
    && EFP_HOST_API_TOKEN=build-check EFP_HOST_RUN_DIR=/tmp/efp-build-check \
        python3 -m efp_app_host.gateway.render_nginx > /tmp/efp-build-check/nginx.conf \
    && nginx -t -c /tmp/efp-build-check/nginx.conf \
    && rm -rf /tmp/efp-build-check

EXPOSE 8080 9000
VOLUME ["/var/lib/efp-host/store"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/_efp/health', timeout=3)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/opt/efp/bin/entrypoint.sh"]
