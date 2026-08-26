#!/bin/bash

OUTPUT_DIR="/app/output"
PLAYLIST="/app/playlist/playlist.txt"

mkdir -p /app/playlist

# Limpa playlist antiga
echo "" > "$PLAYLIST"

# Adiciona todos os blocos de 20s em ordem
for f in $(ls -1 $OUTPUT_DIR/*.mp4 | sort); do
    echo "file '$f'" >> "$PLAYLIST"
done
