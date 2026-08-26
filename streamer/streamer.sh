#!/bin/bash

RTMP_URL="rtmp://a.rtmp.youtube.com/live2/63wy-4fez-j9c0-j0fp-fek6"

echo "[Streamer] Iniciando o motor de transmissão contínua..."

while true; do
  INPUT="/app/output/final.mp4"
  AUDIO="/app/assets/news_audio.wav"
  BACKGROUND_MUSIC="/app/assets/musica_classica.mp3"

  # GATILHO DIRETO: Valida apenas se o arquivo existe e tem tamanho maior que zero
  if [ -f "$INPUT" ]; then
    echo "[Streamer] Video detectado! Transmitindo bloco..."
    
    ffmpeg -re -i "$INPUT" \
      -re -i "$AUDIO" \
      -re -i "$BACKGROUND_MUSIC" \
      -filter_complex "[1:a][2:a]amix=inputs=2:duration=shortest:dropout_transition=3[aout]" \
      -map 0:v -map "[aout]" \
      -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
      -b:v 4500k -maxrate 5000k -bufsize 10000k \
      -g 60 -keyint_min 30 \
      -c:a aac -b:a 128k -ar 48000 -ac 2 \
      -f flv "$RTMP_URL"
      
    echo "[Streamer] Bloco enviado com sucesso. Reiniciando ciclo..."
  else
    echo "[Streamer] Aguardando um final.mp4 valido e completo..."
  fi
  
  sleep 2
done
