FROM python:3.10-slim

RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean

WORKDIR /app

COPY streamer.sh /app/streamer.sh
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt || true

# PERMISSÃO CRÍTICA
RUN chmod +x /app/streamer.sh

CMD ["/app/streamer.sh"]
