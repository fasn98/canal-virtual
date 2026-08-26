#!/bin/bash

# ============================
# ASSETS
# ============================

BASE="/app/assets/base.mp4"
BLOCO="/app/assets/bloco20s.mp4"
LOGO="/app/assets/logo.png"
LOWER="/app/assets/lowerthird.png"
TICKER="/app/assets/ticker.png"
AUDIO="/app/output/news_tts.wav"
OUT="/app/output/final.mp4"

# ============================
# VARIÁVEIS EDITORIAIS
# ============================

TITLE="${FV_TITLE:-FutureVerse News}"
SUBTITLE="${FV_SUBTITLE:-Atualizações em tempo real do estúdio virtual}"
HEADLINE="${FV_HEADLINE:-Nenhuma notícia disponível no momento.}"
TICKER_TEXT="${FV_TICKER:-Aguardando novas atualizações...}"

# Escapar caracteres perigosos
TITLE=$(echo "$TITLE" | sed "s/'/\\\'/g")
SUBTITLE=$(echo "$SUBTITLE" | sed "s/'/\\\'/g")
HEADLINE=$(echo "$HEADLINE" | sed "s/'/\\\'/g")
TICKER_TEXT=$(echo "$TICKER_TEXT" | sed "s/'/\\\'/g")

# ============================
# RENDER
# ============================

bash /opt/canal-virtual/renderer/generate_tts.sh

ffmpeg -y \
  -i "$BASE" \
  -i "$BLOCO" \
  -i "$LOGO" \
  -i "$LOWER" \
  -i "$TICKER" \
  -i "$AUDIO" \
  -filter_complex "\
    [0:v][1:v]overlay=100:800[bg1]; \
    [bg1][2:v]overlay=1600:50[bg2]; \
    [bg2][3:v]overlay=0:880[bg3]; \
    [bg3][4:v]overlay=0:1000[base]; \
    [base]drawtext=text='${TITLE}':fontcolor=white:fontsize=48:x=100:y=60[txt1]; \
    [txt1]drawtext=text='${SUBTITLE}':fontcolor=white:fontsize=32:x=100:y=120[txt2]; \
    [txt2]drawtext=text='${HEADLINE}':fontcolor=yellow:fontsize=40:x=100:y=850[txt3]; \
    [txt3]drawtext=text='${TICKER_TEXT}':fontcolor=white:fontsize=28:x=50:y=1030[txt4]; \
    [txt4]scale=1920:1080,setsar=1[final] \
  " \
  -map "[final]" -map 5 \
  -c:v libx264 -preset slow -crf 18 \
  -b:v 6000k -maxrate 6500k -bufsize 12000k \
  -c:a aac -b:a 128k \
  "$OUT"
