#!/bin/bash

TITLE="$1"
TEXT="$2"
NEWS_ID="$3"

OUTPUT="/app/output/${NEWS_ID}.mp4"

# Caminho dos assets
ASSETS="/app/assets"

ffmpeg -y \
  -f lavfi -i color=c=black:s=1920x1080:r=30 \
  -loop 1 -i "$ASSETS/background.png" \
  -i "$ASSETS/anchor.png" \
  -i "$ASSETS/logo.png" \
  -i "$ASSETS/lowerthird.png" \
  -i "$ASSETS/ticker.png" \
  -i "$ASSETS/dummy_audio.wav" \
  -filter_complex "\
    [0][1]overlay=0:0[bg1]; \
    [bg1][2]overlay=50:200[bg2]; \
    [bg2][3]overlay=1600:50[bg3]; \
    [bg3][4]overlay=0:880[bg4]; \
    [bg4][5]overlay=0:1000[base]; \
    [base]drawtext=text='${TITLE}':fontcolor=white:fontsize=42:x=100:y=850[txt1]; \
    [txt1]drawtext=text='${TEXT}':fontcolor=white:fontsize=28:x=100:y=930[final] \
  " \
  -map "[final]" -map 6 \
  -t 20 \
  -c:v libx264 -preset veryfast -crf 23 \
  -c:a aac -b:a 128k \
  "$OUTPUT"
