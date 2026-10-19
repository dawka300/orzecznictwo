# Wyszukiwarki ETPC i NSA (python -m etpc|nsa www) w kontenerach, żeby wstawały
# razem z dockerem. Kod nie jest kopiowany: docker-compose montuje repozytorium,
# więc po zmianie kodu wystarczy: docker compose restart www www-nsa
FROM python:3.13-slim
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
# Wektor zapytania liczy model w kontenerze (bez LM Studio): torch tylko na CPU.
# Model (BAAI/bge-m3, ~2.3 GB) pobiera się przy pierwszym starcie do wolumenu hfcache.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install --no-cache-dir sentence-transformers
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HF_HUB_DISABLE_TELEMETRY=1
CMD ["python", "-m", "etpc", "www", "--host", "0.0.0.0", "--port", "8080"]
