import time
import redis
import subprocess
import os
import json

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
AVATAR_IMG = f"{ASSETS_DIR}/avatar.png"
LOGO_IMG = f"{ASSETS_DIR}/logo.png"
LOWERTHIRD_IMG = f"{ASSETS_DIR}/lowerthird.png"
DUMMY_AUDIO = f"{ASSETS_DIR}/news_audio.wav"
TICKER_IMG = f"{TICKER_DIR}/ticker.png"

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


def handle_event(event_id, data):
    """Processa UMA mensagem. Usado tanto pelo XREADGROUP (mensagens novas)
    quanto pelo XAUTOCLAIM (mensagens travadas recuperadas). Levanta exceção
    em caso de falha — nesse caso NÃO há XACK e a mensagem segue pendente."""
    title = data.get("title", "Sem Título")
    title_original = data.get("title_original", "")
    category = data.get("category", "Geral")
    text = data.get("commentary", "")
    news_id = data.get("id", "0")

    # Usa o áudio real gerado pelo synthesizer (TTS) quando disponível;
    # cai para o áudio fixo apenas se o TTS falhou ou não veio preenchido.
    requested_audio = data.get("audio_file", "").strip()
    if requested_audio and os.path.exists(requested_audio):
        AUDIO_FILE = requested_audio
    else:
        if requested_audio:
            print(f"{TAG} → AVISO: áudio '{requested_audio}' não encontrado, usando fallback.")
        AUDIO_FILE = DUMMY_AUDIO

    print(f"{TAG} → Processando Notícia ID {news_id}: {title}")

    # Escreve a manchete (lower third) e o texto do ticker em arquivos
    # temporários — evita todo problema de aspas/apóstrofos no filtro do ffmpeg.
    with open(title_txt_file, "w", encoding="utf-8") as f:
        f.write(title)

    with open(ticker_txt_file, "w", encoding="utf-8") as f:
        f.write(f"{category.upper()}  •  {title}")

    # COMPOSIÇÃO DO ESTÚDIO: fundo + apresentador + logo + lower third + ticker rolante
    filter_complex = (
        # Fundo quase-16:9 (1672x941): escala cobrindo o frame e corta o excedente
        # sub-pixel. Sem distorção e sem tarjas — melhor que o scale=1920:1080 puro.
        "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1[bg];"
        "[1:v]scale=270:378[av];"
        # Avatar centralizado entre as duas poltronas centrais da cena nova,
        # com a base tucada atrás do lower third (era overlay=1560:420 na cena antiga).
        "[bg][av]overlay=825:452[bg1];"
        "[2:v]scale=220:83[lg];"
        "[bg1][lg]overlay=40:30[bg2];"
        "[3:v]scale=1920:200[lt];"
        "[bg2][lt]overlay=0:800[bg3];"
        f"[bg3]drawtext=textfile='{title_txt_file}':fontcolor=white:fontsize=44:x=60:y=870[bg4];"
        "[4:v]scale=1920:80[tk];"
        "[bg4][tk]overlay=0:1000[bg5];"
        f"[bg5]drawtext=textfile='{ticker_txt_file}':fontcolor=black:fontsize=28:x=w-mod(t*160\\,w+tw):y=1018[vout]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", BACKGROUND_IMG,
        "-loop", "1", "-i", AVATAR_IMG,
        "-loop", "1", "-i", LOGO_IMG,
        "-loop", "1", "-i", LOWERTHIRD_IMG,
        "-loop", "1", "-i", TICKER_IMG,
        "-i", AUDIO_FILE,
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
