FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app ./app
RUN useradd -r -u 1001 appuser && mkdir -p data && chown appuser data
USER appuser
# Cloud Run sets PORT. Keep ONE worker and max-instances=1 (login throttle, audit file and
# admin-edited access list live in process/local disk).
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips="*"
