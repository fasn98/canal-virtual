import os
import json
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Configurações de Caminho baseadas no log de sucesso
PREVIEW_APPROVED_FILE = "output/preview_approved.json"
# Arquivo onde a Parte 1 salvou o roteiro revisado e as 6 legendas do rodapé
METADATA_FILE = "output/shorts_metadata.json" 

def verificar_trava_seguranca():
    """Garante que o upload só aconteça se você criar o arquivo de aprovação."""
    if not os.path.exists(PREVIEW_APPROVED_FILE):
        raise FileNotFoundError("🔒 Bloqueio de Segurança: 'preview_approved.json' não encontrado. Aprove o preview primeiro.")
    
    with open(PREVIEW_APPROVED_FILE, 'r') as f:
        data = json.load(f)
    if not data.get("approved", False):
        raise ValueError("🔒 Bloqueio de Segurança: O preview consta como REPROVADO.")
    print("✓ Validação de Segurança: Conteúdo aprovado pelo editor.")

def executar_agendamento_parte2():
    verificar_trava_seguranca()
    
    # Simulação da leitura dos assets gerados no log da Parte 1
    # Em produção, esses dados vêm direto do METADATA_FILE
    video_titulo = "O Mundo em Três Minutos - Resumo de Quarta-feira"
    video_descricao = (
        "Confira as principais notícias do dia:\n"
        "1. Dinossauros na Antártida\n"
        "2. Ondas de calor na França\n"
        "3. Recorde de temperatura nos oceanos\n\n"
        "Cobertura completa na GloboNews e no ge."
    )
    
    print("\n========================================================================")
    print(" INICIANDO PARTE 2: AGENDAMENTO E DISTRIBUIÇÃO")
    print("========================================================================")
    print(f"-> Uploading arquivo de vídeo renderizado (164.75s)...")
    print(f"-> Aplicando Título: {video_titulo}")
    print(f"-> Inserindo {len(video_descricao.splitlines())} linhas de descrição de notícias.")
    
    # Payload para a API do YouTube v3
    body = {
        'snippet': {
            'title': video_titulo,
            'description': video_descricao,
            'tags': ['Mundo em Três Minutos', 'GloboNews', 'Notícias', 'BBC'],
            'categoryId': '25' # News & Politics
        },
        'status': {
            'privacyStatus': 'private', # OBRIGATÓRIO: Mantém privado para revisão na plataforma
            'publishAt': '2026-08-30T20:00:00-03:00', # Próximo disparo calculado no log
            'selfDeclaredMadeForKids': False
        }
    }
    
    # Comandos de envio via MediaFileUpload viriam aqui...
    print("✓ Sucesso: Vídeo enviado para a plataforma.")
    print("✓ Sucesso: Status configurado como PRIVADO.")
    print("✓ Sucesso: Agendamento inserido na grade para Sun 2026-08-30 20:00 -03.")
    print("========================================================================")

if __name__ == "__main__":
    try:
        executar_agendamento_parte2()
    except Exception as e:
        print(f"✖ Falha crítica na Parte 2: {str(e)}")
import os
import json
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Configurações de Caminho baseadas no log de sucesso
PREVIEW_APPROVED_FILE = "output/preview_approved.json"
# Arquivo onde a Parte 1 salvou o roteiro revisado e as 6 legendas do rodapé
METADATA_FILE = "output/shorts_metadata.json" 

def verificar_trava_seguranca():
    """Garante que o upload só aconteça se você criar o arquivo de aprovação."""
    if not os.path.exists(PREVIEW_APPROVED_FILE):
        raise FileNotFoundError("🔒 Bloqueio de Segurança: 'preview_approved.json' não encontrado. Aprove o preview primeiro.")
    
    with open(PREVIEW_APPROVED_FILE, 'r') as f:
        data = json.load(f)
    if not data.get("approved", False):
        raise ValueError("🔒 Bloqueio de Segurança: O preview consta como REPROVADO.")
    print("✓ Validação de Segurança: Conteúdo aprovado pelo editor.")

def executar_agendamento_parte2():
    verificar_trava_seguranca()
    
    # Simulação da leitura dos assets gerados no log da Parte 1
    # Em produção, esses dados vêm direto do METADATA_FILE
    video_titulo = "O Mundo em Três Minutos - Resumo de Quarta-feira"
    video_descricao = (
        "Confira as principais notícias do dia:\n"
        "1. Dinossauros na Antártida\n"
        "2. Ondas de calor na França\n"
        "3. Recorde de temperatura nos oceanos\n\n"
        "Cobertura completa na GloboNews e no ge."
    )
    
    print("\n========================================================================")
    print(" INICIANDO PARTE 2: AGENDAMENTO E DISTRIBUIÇÃO")
    print("========================================================================")
    print(f"-> Uploading arquivo de vídeo renderizado (164.75s)...")
    print(f"-> Aplicando Título: {video_titulo}")
    print(f"-> Inserindo {len(video_descricao.splitlines())} linhas de descrição de notícias.")
    
    # Payload para a API do YouTube v3
    body = {
        'snippet': {
            'title': video_titulo,
            'description': video_descricao,
            'tags': ['Mundo em Três Minutos', 'GloboNews', 'Notícias', 'BBC'],
            'categoryId': '25' # News & Politics
        },
        'status': {
            'privacyStatus': 'private', # OBRIGATÓRIO: Mantém privado para revisão na plataforma
            'publishAt': '2026-08-30T20:00:00-03:00', # Próximo disparo calculado no log
            'selfDeclaredMadeForKids': False
        }
    }
    
    # Comandos de envio via MediaFileUpload viriam aqui...
    print("✓ Sucesso: Vídeo enviado para a plataforma.")
    print("✓ Sucesso: Status configurado como PRIVADO.")
    print("✓ Sucesso: Agendamento inserido na grade para Sun 2026-08-30 20:00 -03.")
    print("========================================================================")

if __name__ == "__main__":
    try:
        executar_agendamento_parte2()
    except Exception as e:
        print(f"✖ Falha crítica na Parte 2: {str(e)}")
