#!/bin/bash

set -e

NEWS_FILE="/opt/canal-virtual/volumes/output/news.final"
RENDERER="/opt/canal-virtual/renderer/make_final.sh"
OUTPUT="/opt/canal-virtual/volumes/output/final.mp4"
STREAMER_CONTAINER="streamer"

echo "[update_news] Iniciando ciclo editorial..."

# 1) Extrair título, subtítulo e ticker (versão básica)
# Aqui assumimos que news.final é um texto simples.
# Você pode depois trocar por JSON + jq.

TITLE="FutureVerse News"
SUBTITLE="Atualizações em tempo real do estúdio virtual"

if [ -f "$NEWS_FILE" ]; then
  MAIN_HEADLINE=$(head -n 1 "$NEWS_FILE" | sed 's/"/\\"/g')
  TICKER_TEXT=$(tail -n 5 "$NEWS_FILE" | tr '\n' ' • ' | sed 's/"/\\"/g')
else
  MAIN_HEADLINE="Nenhuma notícia disponível no momento."
  TICKER_TEXT="Aguardando novas atualizações do FutureVerse News."
fi

echo "[update_news] Manchete principal: $MAIN_HEADLINE"
echo "[update_news] Ticker: $TICKER_TEXT"

# 2) Exportar variáveis para o make_final.sh usar com drawtext
export FV_TITLE="$TITLE"
export FV_SUBTITLE="$SUBTITLE"
export FV_HEADLINE="$MAIN_HEADLINE"
export FV_TICKER="$TICKER_TEXT"

# 3) Rodar o renderer
echo "[update_news] Chamando renderer..."
bash "$RENDERER"

if [ ! -f "$OUTPUT" ]; then
  echo "[update_news] ERRO: final.mp4 não foi gerado."
  exit 1
fi

echo "[update_news] final.mp4 gerado com sucesso."


# 4) Reiniciar container do streamer
echo "[update_news] Reiniciando container do streamer..."
docker restart "$STREAMER_CONTAINER"

echo "[update_news] Ciclo editorial concluído."
