import time
import datetime
import redis
import subprocess
import os

# Conexão estável com Redis
r = redis.Redis(host="redis", port=6379, decode_responses=True, socket_timeout=10)

# --- Consumer Group config ---
INPUT_STREAM = "news.ready"
OUTPUT_STREAM = "news.block"
GROUP = "renderer-group"
CONSUMER = os.environ.get("HOSTNAME", "consumer-1")
TAG = "Renderer"

# --- Recuperação automática de mensagens travadas ---
STUCK_TIMEOUT_MS = int(os.environ.get("STUCK_MESSAGE_TIMEOUT_MS", "60000"))
MAX_DELIVERY_ATTEMPTS = int(os.environ.get("MAX_DELIVERY_ATTEMPTS", "3"))
STUCK_SCAN_INTERVAL_SEC = int(os.environ.get("STUCK_SCAN_INTERVAL_SEC", "30"))

_last_stuck_scan = 0.0

# --- Lip-sync opcional (D-ID) ---
# ENABLE_LIPSYNC=false (padrão) => pipeline idêntico ao de hoje (avatar estático).
# ENABLE_LIPSYNC=true  => antes de compor o bloco, tenta gerar um vídeo com
# lip-sync real via D-ID (renderer/lipsync.py, totalmente isolado). Qualquer
# falha cai no avatar estático. Ver renderer/lipsync.py.
ENABLE_LIPSYNC = os.environ.get("ENABLE_LIPSYNC", "false").strip().lower() in (
    "1", "true", "yes", "on",
)

# --- Chroma key do vídeo de lip-sync (D-ID) ---
# O mp4 que volta do D-ID tem o fundo verde de assets/avatar_greenscreen.png
# (mp4 não tem transparência real). Antes do overlay, o renderer remove esse
# verde com chromakey (YUV) + despill, recriando o efeito do recorte
# transparente sem o "retângulo flutuante" do fundo original da foto.
# Tudo via env pra afinar sem rebuild (ver README / .env.example):
#   COLOR      = mesma cor usada em avatar_greenscreen.png (#00B140)
#   SIMILARITY = quão perto do verde ainda conta como fundo (maior = remove mais)
#   BLEND      = suavização da borda do alpha (maior = borda mais macia)
DID_CHROMA_COLOR = os.environ.get("DID_CHROMA_COLOR", "0x00B140").strip()
DID_CHROMA_SIMILARITY = os.environ.get("DID_CHROMA_SIMILARITY", "0.14").strip()
DID_CHROMA_BLEND = os.environ.get("DID_CHROMA_BLEND", "0.06").strip()
# despill: tira o resíduo/halo verde em cabelo e ombros depois do recorte.
DID_CHROMA_DESPILL = os.environ.get("DID_CHROMA_DESPILL", "true").strip().lower() in (
    "1", "true", "yes", "on",
)

# Definições de Diretórios Internos do Container
OUTPUT_DIR = "/app/output"
TICKER_DIR = "/app/ticker"
ASSETS_DIR = "/app/assets"

# Garantir Diretórios em Disco
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TICKER_DIR, exist_ok=True)

# Arquivos de Mídia Mapeados
# studio_bg_novo.png: cena nova (poltronas + mapa-múndi), 1672x941 ~16:9, alta resolução.
# Os fundos antigos (background.png / studio_bg.jpg) ficam em assets/ como fallback/histórico.
BACKGROUND_IMG = f"{ASSETS_DIR}/studio_bg_novo.png"
# Recorte transparente da apresentadora (sem o retângulo de fundo opaco).
# O avatar.png antigo fica em assets/ como histórico/fallback.
AVATAR_IMG = f"{ASSETS_DIR}/avatar_transparent.png"
LOGO_IMG = f"{ASSETS_DIR}/logo.png"
LOWERTHIRD_IMG = f"{ASSETS_DIR}/lowerthird.png"
DUMMY_AUDIO = f"{ASSETS_DIR}/news_audio.wav"
TICKER_IMG = f"{TICKER_DIR}/ticker.png"

# --- TV virtual (b-roll temático no cenário) ---
# Moldura com a "tela" recortada (transparente). Dentro dela roda, em loop, um
# vídeo genérico do Pexels relacionado à CATEGORIA da notícia principal do
# bloco atual — 1 vídeo por bloco de NEWS_PER_PROMO notícias reais. Toda a
# lógica de busca/cache/fallback vive em renderer/tvbroll.py; aqui só ficam a
# geometria da moldura e o controle de "qual vídeo vale para este bloco".
# ENABLE_TV=false desliga tudo (composição idêntica à de hoje).
ENABLE_TV = os.environ.get("ENABLE_TV", "true").strip().lower() in (
    "1", "true", "yes", "on",
)
TV_FRAME_IMG = f"{ASSETS_DIR}/tv_frame.png"
# Geometria de tv_frame.png (520x340). O bezel opaco ocupa as 4 bordas; a área
# transparente da "tela" vai de x:[24..496] y:[24..316] no PNG. Damos 2 px de
# sangria (DX/DY=22, W=477, H=297) para o bezel cobrir a borda serrilhada do
# vídeo — sem isso aparece um fio da imagem por baixo da moldura.
TV_FRAME_W, TV_FRAME_H = 520, 340
TV_SCREEN_DX, TV_SCREEN_DY = 22, 22
TV_SCREEN_W, TV_SCREEN_H = 477, 297
# Canto superior esquerdo da moldura na composição 1920x1080. Lado esquerdo,
# abaixo do logo (que termina ~y=113), à esquerda da apresentadora (x>=940) e
# acima do lower third (y>=800). Ajustável por env sem rebuild.
TV_X = int(os.environ.get("TV_X", "60"))
TV_Y = int(os.environ.get("TV_Y", "180"))
# Índices das entradas extras do ffmpeg quando há TV (ver montagem do cmd):
# 0=fundo 1=avatar 2=logo 3=lowerthird 4=ticker 5=áudio  6=vídeo TV  7=moldura.
TV_VIDEO_IDX, TV_FRAME_IDX = 6, 7

# Um vídeo de b-roll por BLOCO. NEWS_PER_PROMO (mesmo valor do promoter)
# define o tamanho do bloco; o contador vive no Redis e sobrevive a restart.
NEWS_PER_PROMO = int(os.environ.get("NEWS_PER_PROMO", "5"))
TV_BLOCK_POS_KEY = "tv:block_pos"
TV_CURRENT_VIDEO_KEY = "tv:current_video"
TV_CURRENT_CATEGORY_KEY = "tv:current_category"

# Destinos Finais
final_file = f"{OUTPUT_DIR}/final.mp4"
final_temp = f"{OUTPUT_DIR}/final_temp.mp4"
title_txt_file = f"{OUTPUT_DIR}/title_temp.txt"
ticker_txt_file = f"{OUTPUT_DIR}/ticker_temp.txt"


def ensure_group():
    """Cria o consumer group na inicialização. BUSYGROUP (grupo já existe)
    é esperado em restarts e não é um erro real."""
    try:
        r.xgroup_create(INPUT_STREAM, GROUP, id="$", mkstream=True)
        print(f"{TAG} → consumer group '{GROUP}' criado em '{INPUT_STREAM}'.")
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP" in str(e):
            print(f"{TAG} → consumer group '{GROUP}' já existe. OK.")
        else:
            raise


# --- Lower third: geometria da caixa do lowerthird.png (1920x200 @ y=800) ---
# Medido no PNG: a faixa azul visível vai de x=200 a x=1720 e de y=840 a y=962
# na tela (interior útil y≈842..960, centro y=901). O texto de NOTÍCIA agora é
# posicionado DENTRO dessa caixa nas duas bordas (antes começava em x=60, fora).
LT_TEXT_X = 218                           # borda interna esquerda (200) + folga
LT_BOX_RIGHT = 1702                       # borda interna direita (1720) - folga
LT_MAX_TEXT_PX = LT_BOX_RIGHT - LT_TEXT_X  # ~1484 px úteis
LT_BASE_FONTSIZE = 44
LT_MIN_FONTSIZE = 26
LT_BOX_CENTER_Y = 901                     # centro vertical da faixa visível
# Largura média de glifo da fonte sans padrão do ffmpeg (DejaVu Sans), para
# títulos em PT (mistura maiúsc/minúsc + acentos). Conservador de propósito:
# erra para quebrar/reduzir antes de vazar.
LT_GLYPH_RATIO = 0.55

# --- Lower third do bloco PROMOCIONAL (category == "Promoção") ---
# São duas linhas dentro da MESMA caixa do lowerthird.png: o @ do canal
# (linha principal, maior) e, logo abaixo, o selo de call-to-action (menor).
# Antes o texto começava em x=60 (à esquerda da caixa, cujo interior começa
# em ~200) e o bloco ficava baixo demais — visualmente descolado da faixa.
# Agora as duas linhas se alinham à mesma borda interna das manchetes
# (LT_TEXT_X) e o conjunto fica centralizado na altura da faixa
# (LT_BOX_CENTER_Y), exatamente como o layout de notícia.
PROMO_LT_FONTSIZE = 46
PROMO_SEAL_FONTSIZE = 26
_PROMO_MAIN_LINE_H = int(PROMO_LT_FONTSIZE * 1.25)
_PROMO_SEAL_LINE_H = int(PROMO_SEAL_FONTSIZE * 1.25)
# Ajuste óptico fino: medindo um frame renderizado, o bloco centrado só pela
# métrica de linha ainda sobra ~6 px para cima dentro da faixa (folga de
# ~17 px acima do @ contra ~28 px abaixo do selo). Empurra 6 px para baixo
# para as folgas ficarem simétricas.
PROMO_LT_Y_OFFSET = 6
# y (topo) da linha principal: sobe o bloco de 2 linhas até ele ficar
# centrado em LT_BOX_CENTER_Y. O selo vem uma altura de linha abaixo.
PROMO_LT_MAIN_Y = int(
    LT_BOX_CENTER_Y - (_PROMO_MAIN_LINE_H + _PROMO_SEAL_LINE_H) / 2
) + PROMO_LT_Y_OFFSET
PROMO_SEAL_Y = PROMO_LT_MAIN_Y + _PROMO_MAIN_LINE_H


def _approx_text_width(text, fontsize):
    return len(text) * LT_GLYPH_RATIO * fontsize


def _truncate_to_width(text, fontsize, max_px):
    """Garantia dura: corta o texto ao que comprovadamente cabe em max_px."""
    if _approx_text_width(text, fontsize) <= max_px:
        return text
    max_chars = max(1, int(max_px / (LT_GLYPH_RATIO * fontsize)) - 1)
    return text[:max_chars].rstrip() + "…"


def layout_lowerthird(title):
    """Decide (texto, fontsize, y) do lower third para uma manchete de notícia
    NÃO vazar da caixa do lowerthird.png:
      - cabe em 1 linha no fontsize atual (44) -> mantém como está;
      - senão, quebra em 2 linhas no espaço mais próximo da metade E reduz o
        fontsize (até 26) se a linha mais longa ainda não couber;
      - reposiciona o y para o texto seguir centralizado na altura da caixa.
    """
    title = " ".join(title.split())

    def _y_for(fontsize, n_lines):
        line_h = int(fontsize * 1.25)
        return int(LT_BOX_CENTER_Y - (n_lines * line_h) / 2)

    if _approx_text_width(title, LT_BASE_FONTSIZE) <= LT_MAX_TEXT_PX:
        return title, LT_BASE_FONTSIZE, _y_for(LT_BASE_FONTSIZE, 1)

    # Quebra em duas linhas no espaço mais próximo da metade do texto.
    mid = len(title) // 2
    left = title.rfind(" ", 0, mid)
    right = title.find(" ", mid)
    cands = [p for p in (left, right) if p != -1]
    if cands:
        split = min(cands, key=lambda p: abs(p - mid))
        line1, line2 = title[:split].strip(), title[split:].strip()
    else:
        line1, line2 = title, ""

    longest = max(len(line1), len(line2) if line2 else 0)

    fontsize = LT_BASE_FONTSIZE
    while fontsize > LT_MIN_FONTSIZE and longest * LT_GLYPH_RATIO * fontsize > LT_MAX_TEXT_PX:
        fontsize -= 2

    line1 = _truncate_to_width(line1, fontsize, LT_MAX_TEXT_PX)
    if line2:
        line2 = _truncate_to_width(line2, fontsize, LT_MAX_TEXT_PX)

    text = f"{line1}\n{line2}" if line2 else line1
    return text, fontsize, _y_for(fontsize, 2 if line2 else 1)


def build_ticker_text(current_title, current_category):
    """Texto do ticker = as últimas 5 manchetes REAIS (category != "Promoção")
    do stream news.ready, concatenadas com ' • '. Recalculado a cada render.
    Se algo falhar, cai no formato antigo (categoria + título atual)."""
    try:
        entries = r.xrevrange(INPUT_STREAM, "+", "-", count=40)
        titles = []
        seen = set()
        for _id, d in entries:
            cat = (d.get("category") or "").strip().lower()
            if cat in ("promoção", "promocao", "promo"):
                continue
            t = " ".join((d.get("title") or "").split())
            if t and t not in seen:
                seen.add(t)
                titles.append(t)
            if len(titles) >= 5:
                break
        if not titles:
            titles = [current_title] if current_title else []
        return "  •  ".join(titles)
    except Exception as e:
        print(f"{TAG} → AVISO: falha ao montar ticker das últimas manchetes ({e}).", flush=True)
        return f"{current_category.upper()}  •  {current_title}"


def _tv_current_or_none():
    """Lê o vídeo já escolhido para o bloco atual (Redis) e confirma que o
    arquivo existe em disco. Usado no meio do bloco e nos blocos promocionais
    (que não abrem bloco novo)."""
    if not ENABLE_TV:
        return None
    try:
        path = r.get(TV_CURRENT_VIDEO_KEY)
    except Exception:
        return None
    if path and os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    return None


def get_block_tv_video(is_promo, category, news_id, title=None):
    """Decide qual vídeo de b-roll a TV mostra neste render.

    Regra: 1 vídeo por BLOCO de NEWS_PER_PROMO notícias reais.
      - 1ª notícia real do bloco  -> busca um b-roll para a notícia dela
        (renderer/tvbroll.py: 1º o nosso canal no YouTube pela categoria +
        título, senão Pexels pela categoria), grava a escolha no Redis e usa;
      - 2ª..Nª notícia do bloco   -> reusa a escolha gravada;
      - bloco promocional          -> reusa a escolha do bloco (continuidade),
        sem mexer no contador.

    Qualquer falha -> None e o renderer compõe o bloco SEM a TV, como antes.
    O contador `tv:block_pos` vive no Redis (sobrevive a restart) e se
    realinha sozinho ao ciclo do promoter a cada NEWS_PER_PROMO."""
    if not ENABLE_TV:
        return None

    if is_promo:
        return _tv_current_or_none()

    try:
        pos = r.incr(TV_BLOCK_POS_KEY)
    except Exception as e:
        print(f"{TAG} → AVISO: contador de bloco da TV falhou ({e}).", flush=True)
        return _tv_current_or_none()

    if pos >= NEWS_PER_PROMO:
        # A próxima notícia real reinicia o ciclo (alinhado ao promoter).
        try:
            r.set(TV_BLOCK_POS_KEY, 0)
        except Exception:
            pass

    if pos != 1:
        return _tv_current_or_none()

    # 1ª notícia real do bloco: escolhe um vídeo novo para a notícia dela.
    print(
        f"{TAG} → novo bloco da TV (1ª notícia: {category!r} / {title!r}); "
        "buscando b-roll (canal YouTube -> Pexels).",
        flush=True,
    )
    path = None
    try:
        from tvbroll import get_broll_video
        path = get_broll_video(category, title)
    except Exception as e:
        print(f"{TAG} → AVISO: busca de b-roll falhou ({e}); bloco sem TV.", flush=True)
        path = None

    try:
        if path:
            r.set(TV_CURRENT_VIDEO_KEY, path)
            r.set(TV_CURRENT_CATEGORY_KEY, category or "")
        else:
            r.delete(TV_CURRENT_VIDEO_KEY)
            r.delete(TV_CURRENT_CATEGORY_KEY)
    except Exception:
        pass
    return path


def handle_event(event_id, data):
    """Processa UMA mensagem. Usado tanto pelo XREADGROUP (mensagens novas)
    quanto pelo XAUTOCLAIM (mensagens travadas recuperadas). Levanta exceção
    em caso de falha — nesse caso NÃO há XACK e a mensagem segue pendente."""
    title = data.get("title", "Sem Título")
    title_original = data.get("title_original", "")
    category = data.get("category", "Geral")
    text = data.get("commentary", "")
    news_id = data.get("id", "0")

    # Freio de gasto diário da ElevenLabs (marcado pelo synthesizer). Quando o
    # orçamento do dia esgotou, NÃO renderizamos bloco novo para este item e
    # NÃO usamos o áudio dummy: apenas confirmamos a mensagem e seguimos, sem
    # tocar no final.mp4. O streamer continua transmitindo o último bloco bom
    # em loop até o contador virar amanhã (ou até uma notícia curta caber no
    # que sobrou do orçamento).
    budget_exceeded = str(data.get("budget_exceeded", "")).strip().lower() in (
        "1", "true", "yes", "on",
    )
    if budget_exceeded:
        print(
            f"{TAG} → Orçamento diário esgotado, pulando {news_id}, "
            f"mantendo conteúdo atual no ar",
            flush=True,
        )
        r.xack(INPUT_STREAM, GROUP, event_id)
        return

    # Usa o áudio real gerado pelo synthesizer (TTS) quando disponível;
    # cai para o áudio fixo apenas se o TTS falhou ou não veio preenchido.
    requested_audio = data.get("audio_file", "").strip()
    if requested_audio and os.path.exists(requested_audio):
        AUDIO_FILE = requested_audio
    else:
        if requested_audio:
            print(f"{TAG} → AVISO: áudio '{requested_audio}' não encontrado, usando fallback.")
        AUDIO_FILE = DUMMY_AUDIO

    tipo = "CHAMADA PROMO" if category.strip().lower() in ("promoção", "promocao", "promo") else "Notícia"
    # Reprise: item republicado pelo synthesizer porque o orçamento do dia
    # esgotou. Áudio já pago (cai no cache), só o ffmpeg local remonta o vídeo.
    is_reprise = str(data.get("reprise", "")).strip().lower() in ("1", "true", "yes", "on")
    print(
        f"{TAG} → Processando {tipo} ID {news_id}: {title}"
        + (" [REPRISE — orçamento esgotado, áudio já pago]" if is_reprise else "")
    )

    # Lip-sync opcional: só quando ligado e quando há áudio real (mp3) do
    # synthesizer — nunca para o áudio de fallback. Em falha, `lipsync_video`
    # fica None e a composição usa a imagem estática, exatamente como antes.
    lipsync_video = None
    if ENABLE_LIPSYNC and AUDIO_FILE != DUMMY_AUDIO:
        try:
            from lipsync import get_lipsync_video
            lipsync_video = get_lipsync_video(news_id, AUDIO_FILE)
        except Exception as e:
            print(f"{TAG} → AVISO: lip-sync indisponível ({e}); usando avatar estático.", flush=True)

    # Chamada promocional (category == "Promoção"): mesma apresentadora, mesmo
    # fundo e mesmo avatar — a ideia é parecer a mesma âncora fazendo uma chamada
    # do canal irmão, não um anúncio que quebra o clima. Só muda o texto do lower
    # third (vira o @ do canal), o texto do ticker e um selo sutil "INSCREVA-SE".
    is_promo = category.strip().lower() in ("promoção", "promocao", "promo")

    # TV virtual: 1 vídeo de b-roll por bloco (ver get_block_tv_video). Em
    # qualquer falha tv_video fica None e a composição roda sem a TV, igual a hoje.
    tv_video = None
    try:
        tv_video = get_block_tv_video(is_promo, category, news_id, title)
    except Exception as e:
        print(f"{TAG} → AVISO: TV virtual indisponível ({e}); bloco sem TV.", flush=True)
    if tv_video:
        print(f"{TAG} → TV virtual: {tv_video}", flush=True)

    PROMO_LOWERTHIRD = "youtube.com/@FutureVerse-Beyond"
    PROMO_TICKER = (
        "INSCREVA-SE • FUTUREVERSE & BEYOND • +3000 VÍDEOS • CIÊNCIA • "
        "TECNOLOGIA • ASTRONOMIA • AVIAÇÃO • GEOPOLÍTICA • E ALÉM • INSCREVA-SE"
    )

    # Escreve a manchete (lower third) e o texto do ticker em arquivos
    # temporários — evita todo problema de aspas/apóstrofos no filtro do ffmpeg.
    if is_promo:
        # Bloco promocional: @ do canal alinhado à MESMA borda interna das
        # manchetes (LT_TEXT_X) e centralizado na altura da faixa. O selo
        # (INSCREVA-SE • ...) sai logo abaixo, no mesmo x — ver PROMO_SEAL_Y.
        lowerthird_text = PROMO_LOWERTHIRD
        lt_x = str(LT_TEXT_X)
        lt_fontsize = str(PROMO_LT_FONTSIZE)
        lt_y = str(PROMO_LT_MAIN_Y)
        ticker_text = PROMO_TICKER
    else:
        # Manchete: quebra/reduz e posiciona DENTRO da caixa do lowerthird.png.
        lt_text, lt_fs, lt_y_int = layout_lowerthird(title)
        lowerthird_text, lt_x = lt_text, str(LT_TEXT_X)
        lt_fontsize, lt_y = str(lt_fs), str(lt_y_int)
        # Ticker: as últimas 5 manchetes reais, recalculadas a cada render.
        ticker_text = build_ticker_text(title, category)

    with open(title_txt_file, "w", encoding="utf-8") as f:
        f.write(lowerthird_text)

    with open(ticker_txt_file, "w", encoding="utf-8") as f:
        f.write(ticker_text)

    # Parâmetros que diferenciam (sutilmente) o bloco promocional do de notícia:
    # logo um pouco maior, lower third em cor de destaque e fonte maior.
    logo_scale = "300:113" if is_promo else "220:83"
    logo_xy = "40:24" if is_promo else "40:30"
    lt_fontcolor = "0x00E0FF" if is_promo else "white"

    # Filtro da entrada [1:v] (apresentadora): a imagem estática só escala; o
    # vídeo de lip-sync do D-ID vem com fundo verde e precisa ter o verde
    # removido ANTES do overlay — senão o mp4 opaco reintroduz o "retângulo".
    if lipsync_video:
        despill = ",despill=type=green:mix=0.5:expand=0" if DID_CHROMA_DESPILL else ""
        avatar_filter = (
            f"[1:v]scale=410:574,"
            f"chromakey={DID_CHROMA_COLOR}:{DID_CHROMA_SIMILARITY}:{DID_CHROMA_BLEND}"
            f"{despill}[av];"
        )
        print(
            f"{TAG} → chroma key no vídeo de lip-sync: color={DID_CHROMA_COLOR} "
            f"similarity={DID_CHROMA_SIMILARITY} blend={DID_CHROMA_BLEND} "
            f"despill={'on' if DID_CHROMA_DESPILL else 'off'}",
            flush=True,
        )
    else:
        avatar_filter = "[1:v]scale=410:574[av];"

    # COMPOSIÇÃO DO ESTÚDIO: fundo + [TV] + apresentador + logo + lower third + ticker rolante
    filter_parts = [
        # Fundo quase-16:9 (1672x941): escala cobrindo o frame e corta o excedente
        # sub-pixel. Sem distorção e sem tarjas — melhor que o scale=1920:1080 puro.
        "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1[bg];",
    ]

    # TV virtual: o vídeo do Pexels entra ATRÁS da moldura (a moldura faz de
    # "vidro"), e o conjunto vídeo+moldura fica ACIMA do fundo do estúdio mas
    # ABAIXO dos demais gráficos/apresentadora. O vídeo é escalado com
    # cobertura + crop para preencher exatamente o recorte transparente da tela.
    bg_label = "bg"
    if tv_video:
        filter_parts += [
            f"[{TV_VIDEO_IDX}:v]scale={TV_SCREEN_W}:{TV_SCREEN_H}:force_original_aspect_ratio=increase,"
            f"crop={TV_SCREEN_W}:{TV_SCREEN_H},setsar=1[tv];",
            f"[bg][tv]overlay={TV_X + TV_SCREEN_DX}:{TV_Y + TV_SCREEN_DY}[bgtv];",
            f"[{TV_FRAME_IDX}:v]scale={TV_FRAME_W}:{TV_FRAME_H}[tvf];",
            f"[bgtv][tvf]overlay={TV_X}:{TV_Y}[bgtv2];",
        ]
        bg_label = "bgtv2"

    filter_parts += [
        # Recorte da apresentadora (transparente na imagem estática; verde removido
        # por chroma key no vídeo do D-ID). Sem retângulo opaco ela vem maior e
        # mais à frente, sentada na poltrona central-direita da cena nova.
        avatar_filter,
        # base (mãos/braços, onde o recorte tem a borda reta) fica escondida
        # atrás do lower third (y>=800).
        f"[{bg_label}][av]overlay=940:305[bg1];",
        f"[2:v]scale={logo_scale}[lg];",
        f"[bg1][lg]overlay={logo_xy}[bg2];",
        "[3:v]scale=1920:200[lt];",
        "[bg2][lt]overlay=0:800[bg3];",
        f"[bg3]drawtext=textfile='{title_txt_file}':fontcolor={lt_fontcolor}:fontsize={lt_fontsize}:x={lt_x}:y={lt_y}[bg4];",
        "[4:v]scale=1920:80[tk];",
        "[bg4][tk]overlay=0:1000[bg5];",
    ]

    last_label = "bg5"
    if is_promo:
        # Selo sutil logo abaixo do @ do canal: chama a ação sem destacar demais.
        # Mesmo x da linha principal (LT_TEXT_X) e y logo abaixo dela, para o
        # conjunto ficar contido e centrado na caixa do lowerthird.png.
        filter_parts.append(
            f"[bg5]drawtext=text='INSCREVA-SE  •  DEIXE SEU LIKE  •  ATIVE O SININHO':"
            f"fontcolor=0x00E0FF:fontsize={PROMO_SEAL_FONTSIZE}:x={LT_TEXT_X}:y={PROMO_SEAL_Y}[bg6];"
        )
        last_label = "bg6"

    filter_parts.append(
        f"[{last_label}]drawtext=textfile='{ticker_txt_file}':fontcolor=white:fontsize=28:x=w-mod(t*160\\,w+tw):y=1018[vout]"
    )
    filter_complex = "".join(filter_parts)

    # Entrada 1 = apresentadora. Estático: imagem em loop. Lip-sync: o mp4 do
    # D-ID, em loop (-stream_loop -1) para nunca congelar se ficar um pouco mais
    # curto que o áudio; o -shortest lá embaixo corta na duração do áudio.
    # O restante do filter_complex ([1:v]scale=410:574 -> overlay=940:305) vale
    # igual para imagem ou vídeo — mesma posição/tamanho já calibrados.
    if lipsync_video:
        avatar_input = ["-stream_loop", "-1", "-i", lipsync_video]
        print(f"{TAG} → Avatar com LIP-SYNC (D-ID): {lipsync_video}")
    else:
        avatar_input = ["-loop", "1", "-i", AVATAR_IMG]

    # Entradas 6 (vídeo da TV, em loop infinito) e 7 (moldura). Só entram
    # quando há b-roll para o bloco; sem elas o cmd é idêntico ao de hoje.
    # O -shortest lá embaixo corta tudo na duração do áudio.
    tv_inputs = []
    if tv_video:
        tv_inputs = [
            "-stream_loop", "-1", "-i", tv_video,
            "-loop", "1", "-i", TV_FRAME_IMG,
        ]

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", BACKGROUND_IMG,
        *avatar_input,
        "-loop", "1", "-i", LOGO_IMG,
        "-loop", "1", "-i", LOWERTHIRD_IMG,
        "-loop", "1", "-i", TICKER_IMG,
        "-i", AUDIO_FILE,
        *tv_inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "5:a",
        "-r", "30",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        final_temp
    ]

    print(f"{TAG} → Renderizando Bloco Gráfico Unificado...")
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    for tmp in (title_txt_file, ticker_txt_file):
        if os.path.exists(tmp):
            os.remove(tmp)

    if result.returncode != 0:
        print(f"{TAG} → ERRO CRÍTICO NO FFMPEG:")
        print(result.stderr)
        # Levanta exceção: sem XACK, a mensagem segue pendente para reprocessamento.
        raise RuntimeError(f"ffmpeg falhou (rc={result.returncode}) para notícia {news_id}")

    # Entrega Atômica e Definitiva para o Streamer
    if not os.path.exists(final_temp):
        raise RuntimeError(f"final_temp.mp4 não foi gerado para notícia {news_id}")

    os.replace(final_temp, final_file)
    os.chmod(final_file, 0o777)
    print(f"{TAG} → SUCESSO EMISSÃO: {final_file} gerado com sucesso!")

    # Marca da última emissão bem-sucedida, lida pela metrics-api
    # (GET /status/pipeline). Best-effort: nunca quebra a entrega do bloco.
    try:
        r.set(
            "metrics:renderer:last_emission",
            datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            ex=30 * 24 * 3600,
        )
    except Exception:
        pass

    block = {
        "id": news_id,
        "title": title,
        "title_original": title_original,
        "category": category,
        "text": text,
        "video_file": final_file
    }
    r.xadd(OUTPUT_STREAM, block)

    # Só confirma depois do ffmpeg OK, do final.mp4 gravado em disco
    # (os.replace) e do XADD para news.block.
    r.xack(INPUT_STREAM, GROUP, event_id)


def reclaim_stuck():
    """Reivindica (XAUTOCLAIM) mensagens pendentes há mais de STUCK_TIMEOUT_MS
    — tipicamente porque o consumer anterior morreu no meio do processamento —
    e as reprocessa pelo MESMO caminho das mensagens novas (handle_event).
    Mensagens que já excederam MAX_DELIVERY_ATTEMPTS são descartadas com XACK."""
    start_id = "0-0"
    while True:
        resp = r.xautoclaim(
            INPUT_STREAM, GROUP, CONSUMER,
            min_idle_time=STUCK_TIMEOUT_MS, start_id=start_id, count=10,
        )
        cursor, claimed = resp[0], resp[1]

        if claimed:
            try:
                pend = r.xpending_range(INPUT_STREAM, GROUP, min="-", max="+", count=1000)
                attempts_by_id = {p["message_id"]: p["times_delivered"] for p in pend}
            except Exception:
                attempts_by_id = {}

            for event_id, data in claimed:
                attempts = attempts_by_id.get(event_id, 1)

                if attempts > MAX_DELIVERY_ATTEMPTS:
                    r.xack(INPUT_STREAM, GROUP, event_id)
                    print(
                        f"{TAG} → MENSAGEM DESCARTADA APÓS {attempts} TENTATIVAS: "
                        f"{event_id} (id={data.get('id', '?')}, title={data.get('title', '?')!r})",
                        flush=True,
                    )
                    continue

                print(
                    f"{TAG} → RECUPERANDO mensagem travada {event_id} "
                    f"(tentativa {attempts}/{MAX_DELIVERY_ATTEMPTS})",
                    flush=True,
                )
                try:
                    handle_event(event_id, data)
                except Exception as e:
                    print(f"{TAG} → ERRO ao reprocessar {event_id} (continua pendente):", e, flush=True)

        if not cursor or cursor == "0-0":
            break
        start_id = cursor


def maybe_reclaim_stuck():
    """Roda reclaim_stuck() no máximo uma vez a cada STUCK_SCAN_INTERVAL_SEC."""
    global _last_stuck_scan
    now = time.monotonic()
    if now - _last_stuck_scan < STUCK_SCAN_INTERVAL_SEC:
        return
    _last_stuck_scan = now
    try:
        reclaim_stuck()
    except Exception as e:
        print(f"{TAG} → ERRO no scan de mensagens travadas:", e, flush=True)


def main():
    while True:
        try:
            ensure_group()
            break
        except Exception as e:
            print(f"{TAG} → falha ao criar consumer group, tentando de novo:", e)
            time.sleep(3)

    print("Renderer 2D → Motor Gráfico Online. Aguardando news.ready...")
    print(
        f"{TAG} → lip-sync (D-ID): {'ATIVADO' if ENABLE_LIPSYNC else 'desativado'} "
        f"(ENABLE_LIPSYNC={os.environ.get('ENABLE_LIPSYNC', 'false')}).",
        flush=True,
    )
    _yt_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    _yt_on = os.environ.get("ENABLE_TV_YOUTUBE", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )
    _tv_src = (
        f"canal YouTube ({os.environ.get('YOUTUBE_CHANNEL_HANDLE', '@FutureVerse-Beyond')}) "
        "-> Pexels" if (_yt_on and _yt_key) else "Pexels"
    )
    print(
        f"{TAG} → TV virtual (b-roll): "
        f"{'ATIVADA' if ENABLE_TV else 'desativada'} "
        f"(ENABLE_TV={os.environ.get('ENABLE_TV', 'true')}, fonte: {_tv_src}, "
        f"1 vídeo a cada {NEWS_PER_PROMO} notícias, pos={TV_X},{TV_Y}).",
        flush=True,
    )
    print(
        f"{TAG} → recuperação automática ativa "
        f"(timeout={STUCK_TIMEOUT_MS}ms, max_tentativas={MAX_DELIVERY_ATTEMPTS}, "
        f"scan={STUCK_SCAN_INTERVAL_SEC}s).",
        flush=True,
    )

    while True:
        try:
            maybe_reclaim_stuck()

            msgs = r.xreadgroup(GROUP, CONSUMER, {INPUT_STREAM: ">"}, count=1, block=5000)
            if not msgs:
                continue

            for stream, events in msgs:
                for event_id, data in events:
                    try:
                        handle_event(event_id, data)
                    except Exception as e:
                        # Não dá XACK: a mensagem fica pendente para reprocessamento.
                        print(f"{TAG} → ERRO ao processar {event_id} (fica pendente):", e, flush=True)

        except Exception as e:
            print(f"{TAG} MAIN LOOP ERROR:", e, flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
