#!/bin/bash

INPUT="/app/output/final.mp4"

ffmpeg -stream_loop -1 -re \
  -i /app/output/final.mp4 \
  -i /app/assets/musica_classica.mp3 \
  -filter_complex "amix=inputs=2:duration=longest:dropout_transition=2" \
  -c:v libx264 -preset veryfast -b:v 4500k \
  -c:a aac -b:a 128k \
  -f flv "rtmp://a.rtmp.youtube.com/live2/63wy-4fez-j9c0-j0fp-fek6"
