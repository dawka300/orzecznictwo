# Liczenie wektorów paczki na karcie NVIDIA (u kolegi): bge-m3 przez
# sentence-transformers. Koła torch z CUDA 12.8 obsługują też RTX 50xx
# (Blackwell); potrzebny sterownik NVIDIA >= 570.
FROM python:3.12-slim

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cu128 \
 && pip install --no-cache-dir sentence-transformers==6.1.0 transformers==5.20.1 requests

# Model (~2.3 GB) pobiera się przy pierwszym uruchomieniu do wolumenu hfcache.
ENV HF_HOME=/cache PYTHONUNBUFFERED=1
COPY licz_paczke.py /app/licz_paczke.py
WORKDIR /paczki
ENTRYPOINT ["python", "/app/licz_paczke.py"]
