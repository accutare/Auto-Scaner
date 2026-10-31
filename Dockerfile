FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=America/Sao_Paulo

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# /data guarda o banco (controle.db) e clientes.csv: monte como volume persistente
VOLUME ["/data"]
CMD ["python", "-m", "app.main"]
