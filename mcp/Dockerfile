# mcp-gateway — see docs/adr/011-mcp-single-process-multi-mount.md
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir fastapi uvicorn httpx pydantic mcp pyjwt tenacity \
    opentelemetry-api opentelemetry-sdk

COPY observability /app/observability
COPY mcp/gateway /app/gateway

EXPOSE 9100
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "9100"]
