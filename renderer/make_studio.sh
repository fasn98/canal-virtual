#!/bin/bash

BG="/opt/canal-virtual/volumes/assets/studio_bg.jpg"
AVATAR="/opt/canal-virtual/volumes/assets/avatar.png"
OUT="/opt/canal-virtual/volumes/assets/base.mp4"

ffmpeg -y \
  -loop 1 -i "$BG" \
  -loop 1 -i "$AVATAR" \
  -filter_complex "\
    [0:v]scale=2200:1238, \
         crop=1920:1080:(in_w-1920)/2 + 20*sin(t*0.5):(in_h-1080)/2 + 20*cos(t*0.5)[bg]; \
    [1:v]scale=480:-1[av]; \
    [bg][av]overlay=x=1200:y=400" \
  -c:v libx264 -t 600 -pix_fmt yuv420p "$OUT"
