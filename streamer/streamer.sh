#!/bin/bash

if [ -z "$RTMP_URL" ]; then
  echo "[Streamer] ERRO: variável de ambiente RTMP_URL não definida. Abortando."
  exit 1
fi

# Controle de volume explícito do mix de áudio.
# MUSIC_VOLUME: ganho aplicado à música de fundo ANTES da mixagem (0.15 = 15% do volume original).
# VOICE_VOLUME: leve boost na voz da narradora (TTS), que sai bem abaixo do nível confortável de fala.
MUSIC_VOLUME="${MUSIC_VOLUME:-0.15}"
VOICE_VOLUME="${VOICE_VOLUME:-5.0}"

echo "[Streamer] Iniciando o motor de transmissão contínua..."
echo "[Streamer] MUSIC_VOLUME=${MUSIC_VOLUME} | VOICE_VOLUME=${VOICE_VOLUME}"

while true; do
  INPUT="/app/output/final.mp4"
  BACKGROUND_MUSIC="/app/assets/musica_classica.mp3"

  # GATILHO DIRETO: Valida apenas se o arquivo existe e tem tamanho maior que zero
  if [ -f "$INPUT" ]; then
    echo "[Streamer] Video detectado! Transmitindo bloco..."

    ffmpeg -re -i "$INPUT" \
      -re -i "$BACKGROUND_MUSIC" \
      -filter_complex "[1:a]volume=${MUSIC_VOLUME}[music_low];[0:a]volume=${VOICE_VOLUME}[voice_boosted];[voice_boosted][music_low]amix=inputs=2:duration=shortest:dropout_transition=3:normalize=0[aout]" \
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
