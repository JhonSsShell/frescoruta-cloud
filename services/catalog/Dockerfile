# Imagen del microservicio catalog_service (contexto de construcción: raíz del repositorio)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/common \
    PORT=8001

WORKDIR /app

# 1) Dependencias (capa cacheable mientras requirements.txt no cambie)
COPY services/catalog/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# 2) Chasis compartido y código del servicio
COPY common/frescoruta_common ./common/frescoruta_common
COPY services/catalog/catalog_service ./catalog_service

# 3) Usuario sin privilegios y carpeta de datos (SQLite)
RUN useradd --create-home --uid 10001 app && mkdir -p /app/data && chown -R app /app
USER app

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"

# Render inyecta PORT; se respeta para enlazar el puerto correcto.
CMD ["sh", "-c", "exec uvicorn catalog_service.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
