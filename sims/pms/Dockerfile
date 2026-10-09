# pms-sim — see docs/contracts/openapi/pms-sim.openapi.yaml
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1

# python-multipart: pms-sim's own OAuth2 client_credentials token endpoint accepts
# form-encoded fields (fastapi.Form).
RUN pip install --no-cache-dir fastapi uvicorn aiosqlite python-multipart

COPY sims/_shared /app/sims/_shared
COPY sims/pms /app/sims/pms

EXPOSE 9001
CMD ["uvicorn", "sims.pms.main:app", "--host", "0.0.0.0", "--port", "9001"]
