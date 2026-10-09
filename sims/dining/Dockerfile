# dining-sim — see docs/contracts/openapi/dining-sim.openapi.yaml
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir fastapi uvicorn aiosqlite

COPY sims/_shared /app/sims/_shared
COPY sims/dining /app/sims/dining

EXPOSE 9002
CMD ["uvicorn", "sims.dining.main:app", "--host", "0.0.0.0", "--port", "9002"]
