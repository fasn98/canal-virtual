FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Agente só de saída: NÃO expõe porta nenhuma. Fala com o metrics-api e o Redis
# pela rede interna (canalnet) e com o dashboard no Replit por HTTPS de saída.
CMD ["python", "-u", "main.py"]
