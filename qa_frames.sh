#!/bin/bash
V="$1"                       # caminho do mp4 do bloco
OUT=/opt/canal-virtual/previews/qa
mkdir -p "$OUT" && rm -f "$OUT"/*

D=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$V")
echo "duracao: $D s"

for T in 0.5 9 18 18.6 19.2 30; do
  ok=$(echo "$T < $D" | bc -l)
  [ "$ok" = "1" ] && ffmpeg -v error -y -ss "$T" -i "$V" -frames:v 1 -q:v 3 "$OUT/qa_${T}s.jpg"
done

ffmpeg -v error -y -ss 17.5 -t 2 -i "$V" -vf "fps=15,scale=640:-1" "$OUT/loop.gif"
ls -la "$OUT"
