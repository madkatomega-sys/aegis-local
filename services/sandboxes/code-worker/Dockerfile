FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home --shell /usr/sbin/nologin aegis \
 && mkdir -p /workspace && chown 10001:10001 /workspace
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .

USER 10001
VOLUME ["/workspace"]
EXPOSE 8000
CMD ["python", "server.py"]
