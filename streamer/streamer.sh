#!/bin/bash
#
# ============================================================================
# ARQUITETURA: loop que (re)lança o ffmpeg a cada bloco de vídeo.
#
# Aplicadas e confirmadas no ar (diagnóstico de 2026-08-30 — coleta de 45 min
# do eth0 + mtr 200 ciclos provaram que os "engasgos" NÃO eram jitter de rede):
#   * Correção 1 — música com -stream_loop -1 + amix duration=first. Antes, a
#     trilha (34,7s) acabava no meio do bloco (~170s) com duration=shortest e
#     o mux FLV travava: queda de TX de 6-8s a cada ciclo ("evento B").
#     Pós-correção: 0% de amostras em stall (era 5,4%).
#   * Buffer VBV: -maxrate 4700k (teto mais colado no -b:v 4500k) e
#     -bufsize 18000k (~4s), para absorver melhor variação de rede.
#
# PENDENTE — "Correção 2" (conexão RTMP persistente):
#   Objetivo: acabar com o "evento A", o blip de ~2-4s de reconexão RTMP +
#   TCP slow-start que este loop causa a cada ~173s ao relançar o ffmpeg.
#   TENTATIVA de 2026-08-30 (ffmpeg único e persistente alimentado por um
#   demuxer `concat` com playlist estática grande): tecnicamente transmitia
#   (health "good"), MAS o YouTube não promovia para LIVE — ficava preso em
#   "Preparing stream". Além disso, uma variante intermediária (playlist via
#   FIFO) travava o ffmpeg lendo a lista sem fim (0 bytes de saída), e os
#   minutos sem dados fizeram o YouTube AUTO-ENCERRAR o broadcast 24h
#   (enableAutoStop) — teve que subir um broadcast novo.
#   Foi REVERTIDA para esta versão (só Correção 1 + buffer). A versão da
#   tentativa está em streamer/streamer.sh.v2-deferred.
#   Antes de tentar de novo: sessão dedicada, com CAPTURA DO FLV real (tee p/
#   arquivo) + ffprobe -show_packets cruzando um boundary do concat,
#   comparando a estrutura de PTS/DTS contra uma captura boa desta versão,
#   para entender por que o ingest do YouTube não aceita o fluxo como Live.
# ============================================================================

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

    # -stream_loop -1 na música: ela tem ~34,7s e o bloco de vídeo tem ~170s.
    # Sem o loop + com amix duration=shortest a mixagem terminava aos ~35s e o
    # mux FLV engasgava o resto do bloco (queda de TX de 6-8s no meio do ciclo,
    # diagnóstico de 2026-08-30). Agora a música repete e o amix segue a
    # duração do 1º input (vídeo/voz).
    ffmpeg -re -i "$INPUT" \
      -stream_loop -1 -re -i "$BACKGROUND_MUSIC" \
      -filter_complex "[1:a]volume=${MUSIC_VOLUME}[music_low];[0:a]volume=${VOICE_VOLUME}[voice_boosted];[voice_boosted][music_low]amix=inputs=2:duration=first:dropout_transition=3:normalize=0[aout]" \
      -map 0:v -map "[aout]" \
      -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
      -b:v 4500k -maxrate 4700k -bufsize 18000k \
      -g 60 -keyint_min 30 \
      -c:a aac -b:a 128k -ar 48000 -ac 2 \
      -f flv "$RTMP_URL"
      
    echo "[Streamer] Bloco enviado com sucesso. Reiniciando ciclo..."
  else
    echo "[Streamer] Aguardando um final.mp4 valido e completo..."
  fi
  
  sleep 2
done
