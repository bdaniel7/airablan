FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 443
EXPOSE 53/udp
EXPOSE 53/tcp

CMD ["python", "-m", "app.proxy", "--config", "config.yaml"]