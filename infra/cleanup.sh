#!/bin/bash

echo "🧹 Limpando Canal Virtual..."

echo "🔸 Parando containers..."
docker compose down --remove-orphans

echo "🔸 Removendo containers órfãos..."
docker container prune -f

echo "🔸 Removendo redes antigas..."
docker network rm infra_backend 2>/dev/null
docker network rm backend 2>/dev/null

echo "🔸 Removendo volumes não usados..."
docker volume prune -f

echo "🔸 Removendo imagens dangling..."
docker image prune -f

echo "✔ Cleanup completo!"
