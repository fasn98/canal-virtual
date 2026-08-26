FROM python:3.10-slim

# Instalar FFmpeg
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean

# Diretório da aplicação
WORKDIR /app

# Copiar requirements
COPY requirements.txt .

# Instalar dependências
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código do renderer
COPY . .

# Garantir que o script principal é executável
RUN chmod +x /app/main.py

# Diretórios usados pelo pipeline
RUN mkdir -p /opt/canal-virtual/volumes/output
RUN mkdir -p /opt/canal-virtual/volumes/assets

# Comando padrão
CMD ["python", "main.py"]
