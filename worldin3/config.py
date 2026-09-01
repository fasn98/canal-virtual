"""Configuração central do formato "O Mundo em 3 Minutos".

TUDO é lido de variáveis de ambiente, com padrões seguros — nada de horário,
contagem de manchetes ou limite de duração fixo no código (pedido explícito).
Os padrões abaixo são a intenção de produção; sobrescreva no `.env`.
"""

import datetime
import os
import zoneinfo

# --- Anthropic / Claude ---------------------------------------------------
# Mesma chave/modelo do resto do pipeline. O REVIEW_MODEL (padrão: o mesmo do
# comentário) também é o mesmo dos 3 revisores do commentator.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5").strip()
ANTHROPIC_WORKSPACE_ID = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
ANTHROPIC_TIMEOUT_SEC = float(os.environ.get("ANTHROPIC_TIMEOUT_SEC", "120"))

# --- Revisão editorial (reaproveitada do commentator) -------------------
# Mesmo interruptor e mesmo nº de correções do commentator. O formato NÃO tem
# um caminho de revisão próprio: importa `review_commentary` de review.py.
ENABLE_EDITORIAL_REVIEW = os.environ.get(
    "ENABLE_EDITORIAL_REVIEW", "true"
).strip().lower() in ("1", "true", "yes", "on")
MAX_CORRECTION_ATTEMPTS = int(os.environ.get("MAX_CORRECTION_ATTEMPTS", "2"))

# --- Seleção de manchetes ----------------------------------------------
# Fonte: stream `news.ready` (mesma do ticker). Pega as N manchetes REAIS
# (category != "Promoção") mais recentes, sem repetição.
NEWS_READY_STREAM = os.environ.get("WORLDIN3_NEWS_STREAM", "news.ready")
# Se `news.ready` estiver vazio (ex.: synthesizer parado por falta de crédito
# ElevenLabs — ele dá XACK sem publicar), cai neste stream, que tem os mesmos
# campos (id/title/title_original/category/commentary/source) já aprovados pelos
# 3 revisores. Vazio desliga o fallback.
NEWS_FALLBACK_STREAM = os.environ.get("WORLDIN3_NEWS_FALLBACK_STREAM", "news.final")
HEADLINE_COUNT = int(os.environ.get("WORLDIN3_HEADLINE_COUNT", "6"))
# Varre até este nº de entradas recentes procurando manchetes distintas.
HEADLINE_SCAN = int(os.environ.get("WORLDIN3_HEADLINE_SCAN", "120"))
# Nomes de programa/feed que às vezes chegam como "título" e NÃO são manchete.
_DEFAULT_BLOCKLIST = "BBC Inside Science,Tech Now,Tech Life,BBC News,Newscast,Newshour,BBC Verify"
TITLE_BLOCKLIST = [
    t.strip().lower()
    for t in os.environ.get("WORLDIN3_TITLE_BLOCKLIST", _DEFAULT_BLOCKLIST).split(",")
    if t.strip()
]
# Manchete real costuma ter algumas palavras; descarta rótulo curto de seção.
HEADLINE_MIN_WORDS = int(os.environ.get("WORLDIN3_HEADLINE_MIN_WORDS", "4"))

# --- Roteiro -----------------------------------------------------------
# Alvo de palavras. A voz PT-BR da ElevenLabs fala ~1.95 palavras/s (medido),
# então o teto de MAX_SECONDS=170s equivale a ~330 palavras. O modelo tende a
# estourar o alvo em ~10-15%, por isso o padrão fica em 300 (não 430, que vinha
# de uma estimativa antiga de 2.6 pal/s). run_once.py regenera mais curto se o
# áudio real passar de MAX_SECONDS.
TARGET_WORDS = int(os.environ.get("WORLDIN3_TARGET_WORDS", "300"))
# Margem contra a variação natural da fala: teto de 2:50 (não 3:00).
MAX_SECONDS = float(os.environ.get("WORLDIN3_MAX_SECONDS", "170"))
# Quantas vezes regenerar o roteiro mais curto se o áudio estourar MAX_SECONDS.
# 3: com o modelo estourando o alvo, 2 tentativas às vezes não convergem e o
# encerramento (CTA) acabava cortado no teto.
SHORTEN_ATTEMPTS = int(os.environ.get("WORLDIN3_SHORTEN_ATTEMPTS", "3"))

# Encerramento: convida para a live 24h, no MESMO tom da chamada promocional do
# promoter ("Se você gosta do que vê aqui... inscreva-se, deixe seu like...").
CLOSING_CTA = os.environ.get(
    "WORLDIN3_CLOSING_CTA",
    "E se você gosta de acompanhar o mundo assim, de perto, a nossa transmissão "
    "ao vivo roda vinte e quatro horas por dia, sem parar: é só entrar no canal "
    "e assistir a qualquer hora. Inscreva-se, deixe seu like e ative o sininho "
    "para não perder nada.",
).strip()

# --- ElevenLabs (mesma voz/modelo do synthesizer) --------------------
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
ELEVENLABS_MODEL_ID = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
# Freio de gasto PRÓPRIO do formato (chave Redis separada), para não consumir o
# orçamento diário das notícias. Um resumo tem ~2.500-3.000 caracteres; 3x/dia
# ≈ 8-9k. Padrão 12000 já embute margem. O preview pode ignorar com --ignore-budget.
DAILY_CREDIT_BUDGET = int(os.environ.get("WORLDIN3_DAILY_CREDIT_BUDGET", "12000"))

# --- Composição vertical 9:16 ---------------------------------------
OUT_W = int(os.environ.get("WORLDIN3_OUT_W", "1080"))
OUT_H = int(os.environ.get("WORLDIN3_OUT_H", "1920"))
ASSETS_DIR = os.environ.get("WORLDIN3_ASSETS_DIR", "/app/assets")
OUTPUT_DIR = os.environ.get("WORLDIN3_OUTPUT_DIR", "/app/output")
# Mesmos arquivos que o renderer usa hoje.
BACKGROUND_IMG = os.path.join(ASSETS_DIR, "studio_bg_novo.png")
AVATAR_IMG = os.path.join(ASSETS_DIR, "avatar_transparent.png")
LOGO_IMG = os.path.join(ASSETS_DIR, "logo.png")
LOWERTHIRD_IMG = os.path.join(ASSETS_DIR, "lowerthird.png")
# Trilha licenciada em loop — a MESMA que o streamer mistura hoje, com a mesma
# correção (stream_loop -1 + amix duration=first). Ver nota no README/preview.
MUSIC_IMG = os.path.join(ASSETS_DIR, "musica_classica.mp3")
FONT_REGULAR = os.environ.get(
    "WORLDIN3_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
)
FONT_BOLD = os.environ.get(
    "WORLDIN3_FONT_BOLD", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
)
# Deslocamento do recorte do fundo (o fundo é ~16:9 e vira 9:16; 0 = centro).
# Y negativo mostra mais o topo da cena (menos chão). Ajuste sem rebuild.
BG_CROP_X = int(os.environ.get("WORLDIN3_BG_CROP_X", "0"))
BG_CROP_Y = int(os.environ.get("WORLDIN3_BG_CROP_Y", "-150"))
# Geometria da apresentadora na tela vertical (mantém a proporção 270:378 do
# PNG — sem distorção: 980*270/378 = 700). Plano mais fechado que o antigo
# 600x840, para ela ter presença de âncora e não "boiar" no meio do estúdio.
AVATAR_W = int(os.environ.get("WORLDIN3_AVATAR_W", "700"))
AVATAR_H = int(os.environ.get("WORLDIN3_AVATAR_H", "980"))
# Encaixe da apresentadora na faixa de legenda. Na imagem de origem
# (avatar_transparent.png, 270x378) a mão apoiada na bancada vai de y≈308
# (pulso) até y≈358 (pontas dos dedos, praticamente a base do quadro) e o
# antebraço/manga convém por volta de y≈285. Com AVATAR_H=980 a escala é
# 980/378≈2.593. O alvo é a linha de corte (topo da faixa) cair no ANTEBRAÇO
# (y_origem≈275) e a MÃO INTEIRA ficar atrás do cartão OPACO da faixa (a arte
# lowerthird.png, que começa ~55 px abaixo do topo) — nunca cortada no meio.
# TUCK ≈ AVATAR_H - 275*2.593 ≈ 268. overlay_y = CAPTION_Y + TUCK - AVATAR_H;
# o conteúdo dela termina em ~CAPTION_Y + TUCK - 47 (≈1521), acima da base da
# faixa (CAPTION_Y + CAPTION_STRIP_H = 1550).
AVATAR_CAPTION_TUCK = int(os.environ.get("WORLDIN3_AVATAR_CAPTION_TUCK", "268"))
# Faixa de legenda (legível para quem assiste sem som). Fica ACIMA do rodapé:
# o app de Shorts cobre os ~340 px de baixo (handle do canal, descrição,
# botões à direita) — a faixa some se ficar lá embaixo.
CAPTION_Y = int(os.environ.get("WORLDIN3_CAPTION_Y", "1300"))
CAPTION_STRIP_H = int(os.environ.get("WORLDIN3_CAPTION_STRIP_H", "250"))
CAPTION_FONTSIZE = int(os.environ.get("WORLDIN3_CAPTION_FONTSIZE", "46"))
# Opacidade do retângulo de fundo da faixa (por baixo da arte lowerthird.png).
# Subiu de 0.55 -> 0.88: no 0.55 as mãos/antebraços "fantasmas" vazavam pelo
# retângulo na faixa estreita acima do cartão da lowerthird.png. Com 0.88 + o
# cartão (75% alfa) a mão fica ~97% coberta.
CAPTION_BOX_OPACITY = float(os.environ.get("WORLDIN3_CAPTION_BOX_OPACITY", "0.94"))
# Ticker rolante logo abaixo da faixa de legenda — mesmo recurso do formato
# horizontal (renderer), adaptado para 9:16: ocupa a área que sobrava vazia
# entre a faixa e o rodapé. Texto = as manchetes do resumo em rotação (o
# chamador passa `ticker_text`; se não passar, usa as próprias legendas).
ENABLE_TICKER = os.environ.get("WORLDIN3_ENABLE_TICKER", "true").strip().lower() in (
    "1", "true", "yes", "on"
)
TICKER_H = int(os.environ.get("WORLDIN3_TICKER_H", "110"))
TICKER_FONTSIZE = int(os.environ.get("WORLDIN3_TICKER_FONTSIZE", "34"))
TICKER_SPEED = int(os.environ.get("WORLDIN3_TICKER_SPEED", "150"))  # px/s
# Escurecimento em degradê da faixa entre o ticker e a base do quadro (zona
# coberta pela UI do Shorts). Evita o "chão" cinza chapado e sem função: vai
# de BOTTOM_SCRIM_OPACITY no topo até ~+0.22 na base, em 4 degraus.
BOTTOM_SCRIM_OPACITY = float(os.environ.get("WORLDIN3_BOTTOM_SCRIM_OPACITY", "0.34"))
# Mixagem de áudio do arquivo final (o streamer não entra nesse caminho).
VOICE_GAIN = float(os.environ.get("WORLDIN3_VOICE_GAIN", "2.0"))
MUSIC_GAIN = float(os.environ.get("WORLDIN3_MUSIC_GAIN", "0.12"))

# --- Lip-sync D-ID (opcional, mesmo fallback de sempre) --------------
# Interruptor PRÓPRIO: por padrão segue o global ENABLE_LIPSYNC. ATENÇÃO de
# custo: o plano Pro do D-ID rende ~15 min de vídeo/mês; um resumo de 3 min
# 3x/dia estoura isso em ~2 dias. Ligue com consciência (ver preview/README).
ENABLE_LIPSYNC = os.environ.get(
    "WORLDIN3_ENABLE_LIPSYNC", os.environ.get("ENABLE_LIPSYNC", "false")
).strip().lower() in ("1", "true", "yes", "on")

# --- Agendamento (Parte 2: o loop em scheduler.run_forever dispara mesmo) ---
# 3 execuções/dia no horário de Brasília. Configurável, nunca fixo no código.
SCHEDULE_TIMES = [
    t.strip()
    for t in os.environ.get("WORLDIN3_SCHEDULE_TIMES", "08:00,14:00,20:00").split(",")
    if t.strip()
]
SCHEDULE_TZ = os.environ.get("WORLDIN3_SCHEDULE_TZ", "America/Sao_Paulo").strip()
# Janela de tolerância: se o serviço subir/reiniciar poucos minutos DEPOIS de
# um horário, ainda dispara aquela edição (uma vez). Acima disso, espera a
# próxima. Trava anti-duplicata via chave no Redis por slot.
SCHEDULE_CATCHUP_MIN = int(os.environ.get("WORLDIN3_SCHEDULE_CATCHUP_MIN", "20"))

# --- Parte 2: upload no YouTube + playlist + TubeOptimizer -----------------
# OAuth: MESMO client do bot de chat, mas um refresh token SEPARADO, com escopo
# youtube.upload + youtube.force-ssl (ver worldin3/get_upload_token.py). NÃO
# reaproveitar YOUTUBE_OAUTH_REFRESH_TOKEN (aquele é só force-ssl, do chat).
YOUTUBE_OAUTH_CLIENT_ID = os.environ.get("YOUTUBE_OAUTH_CLIENT_ID", "").strip()
YOUTUBE_OAUTH_CLIENT_SECRET = os.environ.get("YOUTUBE_OAUTH_CLIENT_SECRET", "").strip()
YOUTUBE_OAUTH_REFRESH_TOKEN_UPLOAD = os.environ.get(
    "YOUTUBE_OAUTH_REFRESH_TOKEN_UPLOAD", ""
).strip()
YOUTUBE_CHANNEL_HANDLE = os.environ.get("YOUTUBE_CHANNEL_HANDLE", "@FutureVerse-Beyond").strip()
# privacyStatus do upload. Padrão "unlisted" como rede de segurança nas
# primeiras execuções reais; troque para "public" via env quando aprovar.
PRIVACY_STATUS = os.environ.get("WORLDIN3_PRIVACY_STATUS", "unlisted").strip().lower()
# Playlist dedicada ("FutureVerse News — Shorts"). Crie na mão pelo Studio e
# cole o ID aqui (PL...). Vazio => não adiciona a nenhuma playlist.
PLAYLIST_ID = os.environ.get("WORLDIN3_PLAYLIST_ID", "").strip()
# Categoria YouTube: 25 = News & Politics.
YT_CATEGORY_ID = os.environ.get("WORLDIN3_YT_CATEGORY_ID", "25").strip()
YT_TAGS = [
    t.strip() for t in os.environ.get(
        "WORLDIN3_YT_TAGS",
        "O Mundo em 3 Minutos,notícias,resumo de notícias,jornal,mundo,shorts",
    ).split(",") if t.strip()
]
# Link da transmissão ao vivo 24h para a descrição. Vazio => deriva do handle
# (.../<handle>/live sempre resolve para a live corrente do canal).
LIVE_URL = os.environ.get("WORLDIN3_LIVE_URL", "").strip() or (
    f"https://www.youtube.com/{YOUTUBE_CHANNEL_HANDLE}/live"
)

# TubeOptimizer: NUNCA automático nesta fase. Só dispara quando ENABLE for
# ligado E o chamador pedir explicitamente (run_once.py tubeoptimizer).
ENABLE_TUBEOPTIMIZER = os.environ.get(
    "WORLDIN3_ENABLE_TUBEOPTIMIZER", "false"
).strip().lower() in ("1", "true", "yes", "on")
TUBEOPTIMIZER_CONTENT_LINE = os.environ.get(
    "WORLDIN3_TUBEOPTIMIZER_CONTENT_LINE", "futurenews"
).strip()

# Diretório dos artefatos de cada edição (mp4/frame/roteiro/metadata).
def edition_paths(stamp):
    d = OUTPUT_DIR
    return {
        "mp4": os.path.join(d, f"worldin3_edition_{stamp}.mp4"),
        "frame": os.path.join(d, f"worldin3_edition_{stamp}_frame.png"),
        "script": os.path.join(d, f"worldin3_edition_{stamp}_script.txt"),
        "metadata": os.path.join(d, f"worldin3_edition_{stamp}.json"),
        "last": os.path.join(d, "worldin3_edition_last.json"),
    }


def tz():
    try:
        return zoneinfo.ZoneInfo(SCHEDULE_TZ)
    except Exception:
        return datetime.timezone(datetime.timedelta(hours=-3))  # Brasília fallback


def period_label(now=None):
    """Rótulo da edição pelo horário de Brasília: manhã / tarde / noite."""
    now = now or datetime.datetime.now(tz())
    h = now.hour
    if h < 12:
        return "Edição da Manhã"
    if h < 18:
        return "Edição da Tarde"
    return "Edição da Noite"


def anthropic_client():
    """Cliente Anthropic com o MESMO cabeçalho de workspace do commentator."""
    import anthropic

    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY não configurada")
    headers = {}
    if ANTHROPIC_WORKSPACE_ID:
        headers["anthropic-workspace-id"] = ANTHROPIC_WORKSPACE_ID
    return anthropic.Anthropic(
        api_key=ANTHROPIC_API_KEY,
        timeout=ANTHROPIC_TIMEOUT_SEC,
        max_retries=2,
        default_headers=headers or None,
    )
