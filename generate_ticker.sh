#!/bin/bash

TICKER_TXT="/app/ticker/ticker.txt"
TICKER_PNG="/app/ticker/ticker.png"

if [ ! -f "$TICKER_TXT" ]; then
    echo "[Ticker] ERRO: $TICKER_TXT não existe"
    exit 1
fi

ffmpeg -f lavfi -i color=c=0xCC0000:s=1920x90 \
  -vf "
    drawbox=x=0:y=0:w=1920:h=5:color=0x550000:t=fill,
    drawtext=textfile=$TICKER_TXT:
      fontcolor=white:
      fontsize=38:
      font='DejaVuSans-Bold':
      x=20:
      y=25:
      shadowcolor=0x000000:
      shadowx=2:
      shadowy=2
  " \
  -frames:v 1 \
  -y $TICKER_PNG
