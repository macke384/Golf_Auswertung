FROM python:3.13-slim

# tesseract für Foto-Import (deutsches Sprachpaket)
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-deu \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 golf
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir .

USER golf
ENV GOLF_STORAGE_DIR=/data/storage
EXPOSE 8000
# Migrationen beim Start, dann App. --proxy-headers nur mit vertrauenswürdigem Proxy.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=${GOLF_FORWARDED_ALLOW_IPS:-127.0.0.1} --limit-concurrency 100"]
