FROM python:3.14-slim

LABEL org.opencontainers.image.title="LD Maschinenlaufzeit" \
      org.opencontainers.image.description="Laufzeiterfassung für Heidenhain-Steuerungen über LSV2"

# LDM_DATA_DIR statt DB_PATH: So bleibt die Trennung data.db / demo.db (SIMULATE=1) erhalten.
# PUID/PGID: Besitzer der Dateien in /data (Unraid-Standard: nobody:users = 99:100).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONFIG_PATH=/config/config.yaml \
    LDM_DATA_DIR=/data \
    PUID=99 \
    PGID=100 \
    TZ=Europe/Berlin

# tzdata: Systemzeitzonen, damit TZ auch für die Zeitstempel im Log gilt
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tools ./tools
COPY docker/entrypoint.py /entrypoint.py
COPY config.yaml /config/config.yaml

VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/meta', timeout=4)"]

ENTRYPOINT ["python", "/entrypoint.py"]
CMD ["python", "-m", "app", "--host", "0.0.0.0", "--port", "8000"]
