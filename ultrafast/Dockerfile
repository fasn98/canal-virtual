FROM python:3.11-slim

WORKDIR /app

RUN pip install redis

COPY main.py ultrafast_client.py /app/

CMD ["python", "main.py"]
