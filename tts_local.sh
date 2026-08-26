#!/bin/bash

NEWS_FILE="/opt/canal-virtual/volumes/output/news.final"
AUDIO_OUT="/opt/canal-virtual/volumes/assets/news_audio.wav"
MODEL="/opt/piper_models/pt_BR-cadu-medium.onnx"
JSON="/opt/piper_models/pt_BR-cadu-medium.onnx.json"

echo "[tts] Gerando áudio TTS local..."

TEXT=$(cat "$NEWS_FILE")
TEXT=$(echo "$TEXT" | sed "s/'/ /g")

echo "$TEXT" | piper \
    --model "$MODEL" \
    --config "$JSON" \
    --output_file "$AUDIO_OUT"

echo "[tts] Áudio gerado em $AUDIO_OUT"
