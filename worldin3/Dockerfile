# Imagem do formato de Short "O Mundo em 3 Minutos".
#
# ATENÇÃO: o contexto de build deste serviço é a RAIZ do repositório (ver
# docker-compose.yml: build.context = .), diferente dos outros serviços — ele
# precisa copiar, do build, dois arquivos que vivem em OUTROS serviços, para
# REAPROVEITAR o código real em vez de duplicá-lo:
#   - commentator/review.py  -> os 3 revisores (verificador/editorial/aprovador)
#   - renderer/lipsync.py     -> lip-sync D-ID opcional com fallback estático
# Um .dockerignore na raiz mantém o contexto enxuto (sem volumes/, .git, etc).

FROM python:3.10-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY worldin3/requirements.txt ./worldin3/requirements.txt
RUN pip install --no-cache-dir -r worldin3/requirements.txt

COPY worldin3/ ./worldin3/
# Reaproveitamento (NÃO editar as cópias — a fonte da verdade é o outro serviço):
COPY commentator/review.py ./worldin3/review.py
COPY renderer/lipsync.py ./worldin3/lipsync.py
COPY lib/ ./worldin3/lib/

# PARTE 2: o entrypoint padrão é o loop de agendamento (3x/dia -> pipeline +
# upload). O compose sobrescreve `command` para execuções manuais
# (run_once generate / upload-last / tubeoptimizer) e o preview da Parte 1
# continua em `python -m worldin3.preview`. Profile "worldin3" no compose —
# não sobe no `up` normal.
CMD ["python", "-m", "worldin3.scheduler", "--loop"]
