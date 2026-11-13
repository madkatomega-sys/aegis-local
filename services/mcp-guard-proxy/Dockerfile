FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin aegis \
 && mkdir -p /data && chown 10001:10001 /data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY aegis_guard ./aegis_guard

USER 10001
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "aegis_guard.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
