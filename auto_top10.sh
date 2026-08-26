#!/bin/bash

REDIS="docker exec redis redis-cli -n 0"
NEWS_FILE="/opt/canal-virtual/volumes/output/news.final"
RENDERER="/opt/canal-virtual/renderer/make_final.sh"
STREAMER_CONTAINER="streamer"

LIMIT=10

# Lê os últimos itens do stream news.final
RAW_NEWS=$($REDIS XREVRANGE news.final + - COUNT 50)

if [ -z "$RAW_NEWS" ]; then
    echo "Nenhuma notícia disponível no momento." > "$NEWS_FILE"
    $RENDERER
    docker restart $STREAMER_CONTAINER
    exit 0
fi

# Limpa arquivo
echo "" > "$NEWS_FILE"

# Variáveis temporárias
TITLE=""
CATEGORY=""
COMMENTARY=""
COUNT=0

# Processa linha a linha
echo "$RAW_NEWS" | while read -r line; do

    case "$line" in
        title)
            READ_NEXT="TITLE"
            ;;
        category)
            READ_NEXT="CATEGORY"
            ;;
        commentary)
            READ_NEXT="COMMENTARY"
            ;;
        timestamp)
            READ_NEXT=""
            ;;
        id)
            READ_NEXT=""
            ;;
        *)
            # Se estamos lendo o valor de algum campo
            if [ "$READ_NEXT" = "TITLE" ]; then
                TITLE="$line"
                READ_NEXT=""
            elif [ "$READ_NEXT" = "CATEGORY" ]; then
                CATEGORY="$line"
                READ_NEXT=""
            elif [ "$READ_NEXT" = "COMMENTARY" ]; then
                COMMENTARY="$line"
                READ_NEXT=""
            fi
            ;;
    esac

    # Quando temos título + comentário, escrevemos no arquivo
    if [ -n "$TITLE" ] && [ -n "$COMMENTARY" ]; then
        echo "🔹 $TITLE" >> "$NEWS_FILE"
        echo "$COMMENTARY" >> "$NEWS_FILE"
        echo "Categoria: $CATEGORY" >> "$NEWS_FILE"
        echo "" >> "$NEWS_FILE"

        TITLE=""
        CATEGORY=""
        COMMENTARY=""

        COUNT=$((COUNT+1))
        if [ "$COUNT" -ge "$LIMIT" ]; then
            break
        fi
    fi

done

$RENDERER && sleep 1 && docker restart streamer
