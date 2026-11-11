FROM python:3.12-slim
# cups-client: `lp` talks to a CUPS server over a shared /run/cups socket (optional).
# fonts-dejavu-extra carries the condensed faces the VAG and Aisin stickers use.
RUN apt-get update \
 && apt-get install -y --no-install-recommends cups-client fonts-dejavu-core fonts-dejavu-extra \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY car_labels car_labels
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8080", "--timeout", "30", "--access-logfile", "-", "car_labels.app:app"]
