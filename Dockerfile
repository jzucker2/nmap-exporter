FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

RUN apt-get update && apt-get install -y nmap && apt-get autoclean && apt-get autoremove && rm -rf /var/lib/apt/lists/* && rm -rf /var/cache

RUN mkdir /app

ADD requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt && rm /app/requirements.txt

ADD nmap_exporter.py /app/nmap_exporter.py

EXPOSE 8000

CMD ["python", "/app/nmap_exporter.py"]
