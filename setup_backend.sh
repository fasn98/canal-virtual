#!/bin/bash

BASE="/opt/canal-virtual"

echo "📁 Criando estrutura de backend do Canal Virtual..."
mkdir -p $BASE

# Lista de microserviços
SERVICES=(
  "api-gateway"
  "orchestrator"
  "collector"
  "classifier"
  "synthesizer"
  "commentator"
  "presenter"
  "renderer"
  "streamer"
)

# Criar pastas dos microserviços
for SVC in "${SERVICES[@]}"; do
  echo "📦 Criando microserviço: $SVC"
  mkdir -p $BASE/$SVC
  mkdir -p $BASE/$SVC/config
  mkdir -p $BASE/$SVC/utils
  mkdir -p $BASE/$SVC/templates
  mkdir -p $BASE/$SVC/model
  mkdir -p $BASE/$SVC/avatar
  mkdir -p $BASE/$SVC/ffmpeg
  mkdir -p $BASE/$SVC/personas
  mkdir -p $BASE/$SVC/sources
  mkdir -p $BASE/$SVC/playlist

  # Arquivos base
  touch $BASE/$SVC/Dockerfile
  touch $BASE/$SVC/requirements.txt
  touch $BASE/$SVC/main.py
done

echo "📁 Criando estrutura de infraestrutura..."
mkdir -p $BASE/infra
touch $BASE/infra/docker-compose.yml
touch $BASE/infra/env.example
touch $BASE/infra/README.md

echo "📁 Criando volumes persistentes..."
mkdir -p $BASE/volumes/{postgres,redis,minio,prometheus,grafana}

echo "✅ Estrutura criada com sucesso!"
echo "Local: $BASE"
