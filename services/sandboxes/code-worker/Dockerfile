FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home --shell /usr/sbin/nologin aegis \
 && mkdir -p /workspace /venv /cache && chown 10001:10001 /workspace /venv /cache
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py prepare.py ./

USER 10001
# /venv holds the project's dependencies, installed by the workspace-prepare service (prepare.py)
# and mounted read-only by the worker. /cache is pip's download cache, used only by that service.
VOLUME ["/workspace", "/venv", "/cache"]
EXPOSE 8000
CMD ["python", "server.py"]
