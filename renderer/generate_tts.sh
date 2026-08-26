#!/bin/bash

NEWS_FILE="/opt/canal-virtual/volumes/output/news.final"
TTS_OUT="/opt/canal-virtual/volumes/output/news_tts.wav"
MODEL="/opt/piper/models/pt_BR-silvanus-medium.onnx"
JSON="/opt/piper/models/pt_BR-silvanus-medium.onnx.json"

TEXT=$(cat "$NEWS_FILE")

if [ -z "$TEXT" ]; then
    TEXT="Não há notícias disponíveis no momento."
fi

echo "$TEXT" | piper \
    --model "$MODEL" \
    --config "$JSON" \
    --output_file "$TTS_OUT"
