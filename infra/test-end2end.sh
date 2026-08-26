#!/bin/bash

echo "=============================================="
echo "🛰️  CANAL VIRTUAL — TESTE END-TO-END (PRO v2)"
echo "=============================================="
echo ""

LOG_DIR="./test-logs"
mkdir -p $LOG_DIR

TS=$(date +"%Y%m%d_%H%M%S")
RUN_DIR="$LOG_DIR/run_$TS"
mkdir -p "$RUN_DIR"

echo "📁 Logs serão salvos em: $RUN_DIR"
echo ""

echo "🔍 Capturando logs dos microserviços..."
for svc in collector classifier synthesizer commentator presenter renderer streamer; do
    docker logs $svc --tail 500 > "$RUN_DIR/${svc}.log"
done

echo "🔍 Procurando erros de NoneType..."
grep -R "NoneType" "$RUN_DIR" > "$RUN_DIR/errors_NoneType.txt"

echo "🔍 Procurando erros DataError..."
grep -R "DataError" "$RUN_DIR" > "$RUN_DIR/errors_DataError.txt"

echo "🔍 Procurando erros gerais..."
grep -R "ERROR" "$RUN_DIR" > "$RUN_DIR/errors_general.txt"

echo ""
echo "=============================================="
echo "📊 DIAGNÓSTICO AUTOMÁTICO"
echo "=============================================="

if [ -s "$RUN_DIR/errors_DataError.txt" ]; then
    echo "❌ Erro detectado: DataError (NoneType enviado ao Redis)"
    echo "📄 Log: $RUN_DIR/errors_DataError.txt"
    echo ""

    echo "🔍 Identificando microserviço responsável..."
    culprit=$(grep -R "DataError" "$RUN_DIR" | awk -F'/' '{print $3}' | awk -F'.' '{print $1}' | head -n 1)

    echo "👉 Microserviço com erro: $culprit"

    echo ""
    echo "🔍 Identificando linha exata do erro..."
    grep -R "NoneType" "$RUN_DIR/${culprit}.log" > "$RUN_DIR/${culprit}_NoneType_details.txt"
    cat "$RUN_DIR/${culprit}_NoneType_details.txt"

    echo ""
    echo "=============================================="
    echo "🛠️  SUGESTÃO DE CORREÇÃO AUTOMÁTICA"
    echo "=============================================="

    echo "O erro ocorre porque o microserviço '$culprit' enviou um campo None para o Redis."
    echo ""
    echo "✔ A correção é adicionar a função safe() em TODOS os campos enviados ao Redis:"
    echo ""
    echo "    def safe(v):"
    echo "        if v is None:"
    echo "            return \"\""
    echo "        return str(v)"
    echo ""
    echo "E substituir:"
    echo "    r.xadd(\"news.audio\", audio)"
    echo ""
    echo "Por:"
    echo "    audio = { k: safe(v) for k, v in audio.items() }"
    echo "    r.xadd(\"news.audio\", audio)"
    echo ""
else
    echo "✔ Nenhum erro DataError encontrado."
fi

echo ""
echo "=============================================="
echo "🎉 TESTE END-TO-END COMPLETO!"
echo "Logs disponíveis em: $RUN_DIR"
echo "=============================================="
