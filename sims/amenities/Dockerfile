# amenities-sim — see docs/contracts/openapi/amenities-sim.openapi.yaml
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir fastapi uvicorn aiosqlite

COPY sims/_shared /app/sims/_shared
COPY sims/amenities /app/sims/amenities

EXPOSE 9005
CMD ["uvicorn", "sims.amenities.main:app", "--host", "0.0.0.0", "--port", "9005"]
