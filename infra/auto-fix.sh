#!/bin/bash

echo "=============================================="
echo "🛠️  CANAL VIRTUAL — AUTO-FIX REAL"
echo "=============================================="

LOG_DIR="./auto-fix-logs"
mkdir -p $LOG_DIR

TS=$(date +"%Y%m%d_%H%M%S")
RUN_DIR="$LOG_DIR/run_$TS"
mkdir -p "$RUN_DIR"

echo "📁 Logs: $RUN_DIR"
echo ""

echo "🔍 Capturando logs do Presenter..."
docker logs presenter --tail 500 > "$RUN_DIR/presenter.log"

echo "🔍 Procurando erro DataError..."
grep -R "DataError" "$RUN_DIR/presenter.log" > "$RUN_DIR/error.txt"

if [ ! -s "$RUN_DIR/error.txt" ]; then
    echo "✔ Nenhum erro DataError encontrado."
    exit 0
fi

echo "❌ Erro detectado!"
cat "$RUN_DIR/error.txt"

echo ""
echo "🔧 Aplicando patch automático no Presenter..."

TARGET="/opt/canal-virtual/presenter/main.py"

sed -i '/r.xadd/s/out/out = { k: "" if v is None else str(v) for k, v in out.items() }\n    r.xadd/' "$TARGET"

echo "🔄 Reiniciando Presenter..."
docker restart presenter

sleep 3

echo "🔍 Validando..."
docker logs presenter --tail 50 > "$RUN_DIR/postfix.log"

if grep -R "DataError" "$RUN_DIR/postfix.log"; then
    echo "❌ O erro ainda persiste!"
else
    echo "✔ Correção aplicada com sucesso!"
fi

echo ""
echo "=============================================="
echo "🎉 AUTO-FIX COMPLETO!"
echo "Logs disponíveis em: $RUN_DIR"
echo "=============================================="
