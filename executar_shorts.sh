#!/bin/bash

# ==============================================================================
# PIPELINE AUTOMATIZADO: SHORTS "O MUNDO EM TRÊS MINUTOS"
# Executa a Parte 1 (Geração) e a Parte 2 (Distribuição) com travas de segurança
# ==============================================================================

# Definição de Cores para o Terminal
VERDE='\033[0;32m'
AMARELO='\033[1;33m'
VERMELHO='\033[0;31m'
AZUL='\033[0;34m'
NC='\033[0;m' # Sem Cor

BASE_DIR="/opt/canal-virtual"
OUTPUT_DIR="${BASE_DIR}/volumes/output"
TRAVA_SEGURANCA="${OUTPUT_DIR}/preview_approved.json"
SCRIPT_UPLOAD="${BASE_DIR}/promoter/part2_upload.py"

clear
echo -e "${AZUL}========================================================================${NC}"
echo -e "${AZUL}          INICIANDO PIPELINE AUTOMÁTICO: O MUNDO EM TRÊS MINUTOS        ${NC}"
echo -e "${AZUL}========================================================================${NC}"

# ------------------------------------------------------------------------------
# PASSO 1: Garantir que estamos no diretório correto e limpar travas antigas
# ------------------------------------------------------------------------------
cd "$BASE_DIR" || { echo -e "${VERMELHO}✖ Erro: Diretório ${BASE_DIR} não encontrado.${NC}"; exit 1; }

if [ -f "$TRAVA_SEGURANCA" ]; then
    echo -e "${AMARELO}-> Removendo trava de segurança de execuções anteriores...${NC}"
    rm -f "$TRAVA_SEGURANCA"
fi

# ------------------------------------------------------------------------------
# PASSO 2: Executar a Parte 1 (Coleta, Síntese e Renderização do Vídeo)
# ------------------------------------------------------------------------------
echo -e "\n${AMARELO}[1/3] Executando PARTE 1: Gerando roteiro e renderizando preview...${NC}"
docker compose --profile manual run --rm worldin3 python -m worldin3.preview

if [ $? -ne 0 ]; then
    echo -e "\n${VERMELHO}✖ Erro Crítico: Falha na execução da Parte 1.${NC}"
    exit 1
fi

# ------------------------------------------------------------------------------
# PASSO 3: Validação Editorial Humana (Pausa para leitura do roteiro)
# ------------------------------------------------------------------------------
echo -e "\n${AZUL}========================================================================${NC}"
echo -e "${VERDE}✓ PARTE 1 CONCLUÍDA COM SUCESSO!${NC}"
echo -e "${AZUL}========================================================================${NC}"

if [ -f "${OUTPUT_DIR}/worldin3_preview_script.txt" ]; then
    echo -e "${AMARELO}Revisão do Roteiro Gerado:${NC}"
    echo -e "------------------------------------------------------------------------"
    cat "${OUTPUT_DIR}/worldin3_preview_script.txt"
    echo -e "------------------------------------------------------------------------"
else
    echo -e "${VERMELHO}⚠️ Alerta: Arquivo de texto do roteiro não foi localizado para exibição.${NC}"
fi

echo -e "\n${AMARELO}Verifique o vídeo gerado em: ${OUTPUT_DIR}/worldin3_preview.mp4${NC}"
echo -e "Deseja aprovar este conteúdo e disparar o agendamento no YouTube (Parte 2)?"
read -p "(y/n): " confirmacao

if [[ "$confirmacao" != "y" && "$confirmacao" != "Y" ]]; then
    echo -e "\n${VERMELHO}✖ Execução abortada pelo usuário. Nada foi enviado para o YouTube.${NC}"
    exit 0
fi

# ------------------------------------------------------------------------------
# PASSO 4: Criar a trava de aprovação e disparar a Parte 2 (Upload)
# ------------------------------------------------------------------------------
echo -e "\n${AMARELO}[2/3] Gravando chave de aprovação de segurança...${NC}"
echo '{"approved": true}' > "$TRAVA_SEGURANCA"

echo -e "${AMARELO}[3/3] Executando PARTE 2: Injetando script e disparando upload...${NC}"
docker compose run --rm -v "${SCRIPT_UPLOAD}:/app/part2_upload.py" worldin3 python /app/part2_upload.py

if [ $? -eq 0 ]; then
    echo -e "\n${VERDE}========================================================================${NC}"
    echo -e "${VERDE}   ✓ PIPELINE FINALIZADO: Vídeo agendado como PRIVADO com sucesso!    ${NC}"
    echo -e "${VERDE}========================================================================${NC}"
else
    echo -e "\n${VERMELHO}✖ Erro Crítico: Falha no upload da Parte 2.${NC}"
    exit 1
fi

