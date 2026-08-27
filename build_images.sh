#!/bin/bash

BASE="/opt/canal-virtual"

SERVICES=(
  "api-gateway"
  "orchestrator"
  "collector"
  "classifier"
  "synthesizer"
  "commentator"
  "renderer"
  "streamer"
)

echo "🔧 Iniciando build das imagens..."

for SVC in "${SERVICES[@]}"; do
  echo "📦 Buildando imagem: fasn/$SVC:latest"
  cd $BASE/$SVC
  docker build -t fasn/$SVC:latest .
done

echo "✅ Todas as imagens foram buildadas com sucesso!"
