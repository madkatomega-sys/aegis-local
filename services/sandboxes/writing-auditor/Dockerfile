FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin aegis \
 && mkdir -p /data && chown 10001:10001 /data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY vault.py server.py ./

USER 10001
VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "server.py"]
