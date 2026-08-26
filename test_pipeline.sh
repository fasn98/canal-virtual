#!/bin/bash

echo "=== TESTE DO PIPELINE CANAL VIRTUAL ==="

echo "[1] Verificando Redis..."
docker exec redis redis-cli PING || { echo "Redis OFFLINE"; exit 1; }

echo "[2] Limpando stream de teste..."
docker exec redis redis-cli XTRIM news.audio MAXLEN 0

echo "[3] Enviando evento de teste..."
docker exec redis redis-cli XADD news.audio * id test001 title "Teste pipeline" text "Pipeline funcionando" audio_file "dummy.wav"

echo "[4] Aguardando renderer..."
sleep 2

echo "[5] Verificando logs do renderer..."
docker logs renderer | tail -n 20

echo "[6] Verificando arquivo final.mp4..."
if [ -f "./volumes/output/final.mp4" ]; then
    echo "✔ final.mp4 GERADO"
else
    echo "✘ final.mp4 NÃO GERADO"
fi

echo "[7] Verificando streamer..."
docker logs streamer | tail -n 20

echo "=== FIM DO TESTE ==="
