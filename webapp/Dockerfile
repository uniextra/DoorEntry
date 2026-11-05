FROM python:3.11-slim

LABEL maintainer="DoorEntry Webapp"
LABEL description="Legrand / BTicino Door Entry Classe 100X / 300X Web Client"

WORKDIR /app

# Instalar dependencias del sistema mínimas
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Crear usuario y grupo no privilegiado 'appuser'
RUN groupadd -g 1001 appuser && \
    useradd -u 1001 -g appuser -m -s /bin/sh appuser

# Copiar dependencias de Python
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copiar código del backend y frontend
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/

# Directorio de datos para persistencia de credenciales (montado por volumen)
ENV DATA_DIR=/app/data
RUN mkdir -p /app/data && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

WORKDIR /app/backend

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/api/status || exit 1

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
