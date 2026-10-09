# ticketing-sim — see docs/contracts/openapi/ticketing-sim.openapi.yaml
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1

# python-multipart: ticketing-sim's own OAuth2 token endpoint accepts form-encoded
# fields. httpx: outbound fulfillment webhook delivery (sims/_shared/webhook_harness.py
# / the async purchase path's retry-with-backoff delivery).
RUN pip install --no-cache-dir fastapi uvicorn aiosqlite python-multipart httpx

COPY sims/_shared /app/sims/_shared
COPY sims/ticketing /app/sims/ticketing

EXPOSE 9003
CMD ["uvicorn", "sims.ticketing.main:app", "--host", "0.0.0.0", "--port", "9003"]
