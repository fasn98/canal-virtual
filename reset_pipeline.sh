#!/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# ============================================
# Canal Virtual – Reset Pipeline PRO
# Fabio Edition
# ============================================

LOGFILE="/opt/canal-virtual/pipeline_reset.log"
LOCKFILE="/tmp/reset_pipeline.lock"
TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")

# ============================================
# 0. Lockfile – evita execução simultânea
# ============================================
if [ -e "$LOCKFILE" ]; then
    echo "[$TIMESTAMP] Script já está rodando, saindo..." >> $LOGFILE
    exit 0
fi

touch "$LOCKFILE"
trap "rm -f $LOCKFILE" EXIT

echo "[$TIMESTAMP] Iniciando verificação PRO do pipeline..." >> $LOGFILE

# ============================================
# Função: Reiniciar serviço com log
# ============================================
restart_service() {
  SERVICE=$1
  echo "[$TIMESTAMP] Reiniciando $SERVICE..." >> $LOGFILE
  docker restart $SERVICE >> $LOGFILE 2>&1
}

# ============================================
# 1. Verificar saúde do Docker
# ============================================
docker info > /dev/null 2>&1
if [ $? -ne 0 ]; then
  echo "[$TIMESTAMP] Docker não está respondendo! Abortando..." >> $LOGFILE
  exit 1
fi

# ============================================
# 2. Testar Redis (ping)
# ============================================
echo "[$TIMESTAMP] Testando Redis..." >> $LOGFILE
docker exec redis redis-cli ping > /dev/null 2>&1
if [ $? -ne 0 ]; then
  echo "[$TIMESTAMP] Redis não responde. Reiniciando Redis..." >> $LOGFILE
  restart_service "redis"
fi

# ============================================
# 3. Verificar presenter (erro clássico DNS)
# ============================================
echo "[$TIMESTAMP] Verificando presenter..." >> $LOGFILE
docker logs presenter --tail 50 | grep -q "Temporary failure in name resolution"
if [ $? -eq 0 ]; then
  echo "[$TIMESTAMP] Presenter perdeu DNS interno. Reiniciando presenter + synthesizer..." >> $LOGFILE
  restart_service "presenter"
  restart_service "synthesizer"
fi

# ============================================
# 4. Verificar renderer (sem logs = travado)
# ============================================
echo "[$TIMESTAMP] Verificando renderer..." >> $LOGFILE
docker logs renderer --tail 20 | grep -q .
if [ $? -ne 0 ]; then
  echo "[$TIMESTAMP] Renderer sem logs. Reiniciando..." >> $LOGFILE
  restart_service "renderer"
fi

# ============================================
# 5. Verificar streamer (sem frames)
# ============================================
echo "[$TIMESTAMP] Verificando streamer..." >> $LOGFILE
docker logs streamer --tail 50 | grep -q "frame="
if [ $? -ne 0 ]; then
  echo "[$TIMESTAMP] Streamer sem frames. Reiniciando..." >> $LOGFILE
  restart_service "streamer"
fi

# ============================================
# 6. Verificar final.mp4 congelado
# ============================================
FINAL="/opt/canal-virtual/volumes/output/final.mp4"
LAST_MOD=$(stat -c %Y "$FINAL")
NOW=$(date +%s)
DIFF=$((NOW - LAST_MOD))

if [ $DIFF -gt 600 ]; then
  echo "[$TIMESTAMP] final.mp4 congelado há mais de 10 minutos. Reiniciando renderer + presenter..." >> $LOGFILE
  restart_service "renderer"
  restart_service "presenter"
fi

# ============================================
# 7. Limpeza segura de arquivos antigos
# ============================================
echo "[$TIMESTAMP] Limpando arquivos antigos..." >> $LOGFILE
docker exec renderer bash -c 'ps aux | grep -q "python main.py" && find /app/output -type f -mmin +120 -delete'

# ============================================
# 8. Verificar collector (sem logs recentes)
# ============================================
echo "[$TIMESTAMP] Verificando collector..." >> $LOGFILE
docker logs collector --tail 50 | grep -q .
if [ $? -ne 0 ]; then
  echo "[$TIMESTAMP] Collector sem logs. Reiniciando..." >> $LOGFILE
  restart_service "collector"
fi

echo "[$TIMESTAMP] Verificação PRO concluída." >> $LOGFILE
