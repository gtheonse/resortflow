# casino-sim — see docs/contracts/openapi/casino-sim.openapi.yaml (read-only)
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir fastapi uvicorn aiosqlite

COPY sims/_shared /app/sims/_shared
COPY sims/casino /app/sims/casino

EXPOSE 9004
CMD ["uvicorn", "sims.casino.main:app", "--host", "0.0.0.0", "--port", "9004"]
