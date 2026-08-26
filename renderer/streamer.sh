#!/bin/bash

INPUT="/opt/canal-virtual/volumes/output/final.mp4"
AUDIO="/opt/canal-virtual/volumes/assets/news_audio.wav"
RTMP_URL="rtmps://a.rtmp.youtube.com/live2/63wy-4fez-j9c0-j0fp-fek6"

ffmpeg -re \
  -i "$INPUT" \
  -i "$AUDIO" \
  -i "/opt/canal-virtual/volumes/assets/musica_classica.mp3" \
  -filter_complex "[1:a][2:a]amix=inputs=2:duration=longest:dropout_transition=3[aout]" \
  -map 0:v -map "[aout]" \
  -c:v libx264 -preset veryfast -tune zerolatency \
  -b:v 4500k -maxrate 5000k -bufsize 10000k \
  -g 50 -keyint_min 50 \
  -c:a aac -b:a 128k -ar 48000 -ac 2 \
  -f flv "$RTMP_URL"
