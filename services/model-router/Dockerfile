FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin aegis
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY aegis_router ./aegis_router

USER 10001
EXPOSE 8000
CMD ["uvicorn", "aegis_router.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
