#!/bin/bash

ffmpeg -f lavfi -i color=c=red:s=1920x80 \
  -vf "drawtext=textfile=/app/ticker/ticker.txt:fontcolor=white:fontsize=32:x=w-mod(max(t\,0)*200\,w):y=20" \
  -y /app/ticker/ticker.png
