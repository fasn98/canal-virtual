FROM python:3.10-slim

# Instalar FFmpeg
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean

# Diretório da aplicação
WORKDIR /app

# Copiar requirements
COPY renderer/requirements.txt .

# Instalar dependências
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código do renderer
COPY renderer/ .

# lib/gpu_client.py — usado por musetalk.py quando AVATAR_PROVIDER=musetalk.
# Contexto de build agora é a raiz do repo (ver docker-compose.yml) só por
# causa disso; o resto do serviço é idêntico a antes.
COPY lib/ ./lib/

# Garantir que o script principal é executável
RUN chmod +x /app/main.py

# Diretórios usados pelo pipeline
RUN mkdir -p /opt/canal-virtual/volumes/output
RUN mkdir -p /opt/canal-virtual/volumes/assets

# Comando padrão
CMD ["python", "main.py"]
