# Wyszukiwarka ETPC (python -m etpc www) w kontenerze, żeby wstawała razem
# z dockerem. Kod nie jest kopiowany: docker-compose montuje repozytorium,
# więc po zmianie kodu wystarczy: docker compose restart www
FROM python:3.13-slim
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "etpc", "www", "--host", "0.0.0.0", "--port", "8080"]
