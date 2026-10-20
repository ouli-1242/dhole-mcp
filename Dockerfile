# dhole over streamable HTTP. Two stages: build the wheel once, then install it
# into a runtime image without compilers or pip's download cache.
#
# The default image carries the HTTP tier (fetch / crawl / search / PDF /
# reranker) AND patchright's Chromium, so the stealthy browser tier works out
# of the box. Build with --build-arg INSTALL_BROWSER=0 for a smaller HTTP-only
# image; that is the opt-out, not the default.
FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY pyproject.toml README.md LICENSE NOTICE.ddgs.txt ./
COPY src ./src
RUN python -m pip wheel --retries 10 --timeout 60 --wheel-dir /wheels .

FROM python:3.12-slim-bookworm

ARG INSTALL_BROWSER=1
ARG VERSION=17.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    DHOLE_HOME=/data \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

LABEL org.opencontainers.image.title="dhole-mcp" \
      org.opencontainers.image.description="Keyless web fetch, crawl, and multi-engine search for AI agents" \
      org.opencontainers.image.source="https://github.com/ouli-1242/dhole-mcp" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels dhole-mcp \
    && rm -rf /wheels

# Chromium is built in by default: a hosted deployment that silently lacks the
# stealthy tier is the failure mode this image exists to avoid. INSTALL_BROWSER=0
# is the explicit opt-out for a smaller HTTP-only image.
RUN if [ "$INSTALL_BROWSER" = "1" ]; then \
        python -m patchright install --with-deps chromium \
        && rm -rf /var/lib/apt/lists/*; \
    fi

RUN useradd --create-home --uid 10001 dhole \
    && mkdir -p /data \
    && (chown -R dhole:dhole /data /opt/ms-playwright 2>/dev/null || true)

USER dhole
WORKDIR /home/dhole
VOLUME ["/data"]
EXPOSE 8765

# TCP only: the /mcp endpoint answers 406 to a bare GET, so an HTTP probe would
# report a healthy server as sick. A listening socket is what "the process is
# serving" means here.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import socket; s=socket.create_connection(('127.0.0.1',8765),2); s.close()"]

ENTRYPOINT ["dhole"]
CMD ["--http", "--host", "0.0.0.0", "--port", "8765"]
