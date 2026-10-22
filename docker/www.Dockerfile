# Wyszukiwarki ETPC i NSA pod jednym adresem (python -m portal) w kontenerze, żeby
# wstawały razem z dockerem. Kod nie jest kopiowany: docker-compose montuje
# repozytorium, więc po zmianie kodu wystarczy: docker compose restart www
FROM python:3.13-slim
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
# Wektor zapytania liczy model w kontenerze (bez LM Studio): torch tylko na CPU.
# Model (BAAI/bge-m3, ~2.3 GB) pobiera się przy pierwszym starcie do wolumenu hfcache.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install --no-cache-dir sentence-transformers
# Narzędzia deweloperskie (lokalny Python usunięty: testy i ruff też w kontenerze).
RUN pip install --no-cache-dir pytest ruff
# Serwery MCP (python -m etpc|nsa mcp, .mcp.json) uruchamiane w kontenerze.
RUN pip install --no-cache-dir "mcp>=2.3"
# Pobieranie z HUDOC (ETPC_TRANSPORT, łańcuch requests -> curl -> playwright):
# curl przechodzi przez Cloudflare, gdy TLS Pythona nie; Chromium (~500 MB), gdy
# zatrzymany jest też curl. --with-deps doinstalowuje biblioteki systemowe przeglądarki.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir playwright \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
# Kopia bazy (kopia, po pobierz/przetworz): mariadb-dump do serwera db
# (<PREFIKS>_DUMP_COMMAND w docker-compose.yml).
RUN apt-get update && apt-get install -y --no-install-recommends mariadb-client \
    && rm -rf /var/lib/apt/lists/*
# Analiza dokumentu na stronach: tekst z PDF (czysty Python). DOCX czyta biblioteka standardowa.
RUN pip install --no-cache-dir "pypdf>=5"
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HF_HUB_DISABLE_TELEMETRY=1
CMD ["python", "-m", "portal", "--host", "0.0.0.0", "--port", "8080"]
