FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/app/data/models

WORKDIR /app

RUN groupadd --gid 10001 queryforge \
    && useradd --uid 10001 --gid 10001 \
       --create-home queryforge \
    && mkdir -p /app/data \
    && chown -R queryforge:queryforge /app

COPY requirements.txt constraints.txt ./

RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=queryforge:queryforge app ./app
COPY --chown=queryforge:queryforge docs ./docs

USER queryforge

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
