FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/bot \
    CUSTOMER_SUPPORT_CONFIG=/runtime/config.json

WORKDIR /app
COPY requirements.txt requirements-docker.txt ./
RUN pip install -r requirements-docker.txt \
    && useradd --create-home --uid 1000 bot \
    && mkdir -p /runtime /app/workspace/memory /app/workspace/knowledge \
    && chown -R bot:bot /runtime /app /home/bot

COPY --chown=bot:bot customer_gateway.py shop_registry.py shops.json ./
COPY --chown=bot:bot mcp_servers/ ./mcp_servers/
COPY --chown=bot:bot deploy/ ./deploy/
COPY --chown=bot:bot workspace/AGENTS.md ./workspace/AGENTS.md
COPY --chown=bot:bot workspace/skills/ ./workspace/skills/
COPY --chown=bot:bot workspace/knowledge/ ./workspace/knowledge/

USER bot
EXPOSE 18790
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "/app/deploy/healthcheck.py"]
ENTRYPOINT ["python", "/app/deploy/entrypoint.py"]
CMD ["python", "/app/customer_gateway.py"]
