FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FEEFLOW_ENV=production

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY migrations ./migrations

RUN useradd --create-home --uid 10001 feeflow
USER feeflow

EXPOSE 8000
# One worker: the rate limiter and background job loop are in-process.
# --proxy-headers so rate limits and logs see the real client IP behind the host's HTTPS proxy.
CMD ["sh", "-c", "uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
