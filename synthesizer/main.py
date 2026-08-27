import time
import os
import redis
import json
import requests

r = redis.Redis(host="redis", port=6379, decode_responses=True, socket_timeout=10)

# --- Consumer Group config ---
INPUT_STREAM = "news.final"
OUTPUT_STREAM = "news.ready"
GROUP = "synthesizer-group"
CONSUMER = os.environ.get("HOSTNAME", "consumer-1")
TAG = "Synthesizer"

# --- Recuperação automática de mensagens travadas ---
STUCK_TIMEOUT_MS = int(os.environ.get("STUCK_MESSAGE_TIMEOUT_MS", "60000"))
MAX_DELIVERY_ATTEMPTS = int(os.environ.get("MAX_DELIVERY_ATTEMPTS", "3"))
STUCK_SCAN_INTERVAL_SEC = int(os.environ.get("STUCK_SCAN_INTERVAL_SEC", "30"))

_last_stuck_scan = 0.0

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
ELEVENLABS_MODEL_ID = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")

AUDIO_DIR = "/app/assets/audio"
os.makedirs(AUDIO_DIR, exist_ok=True)


def safe(v):
    if v is None:
        return ""
    return str(v)


def synthesize_audio(text, news_id):
    """
    Gera o áudio real da notícia via ElevenLabs TTS.
    Retorna o caminho do arquivo gerado, ou "" em caso de falha
    (o renderer usa um áudio padrão como fallback quando recebe "").
    """
    if not text.strip():
        print(f"{TAG} → Texto vazio para {news_id}, pulando TTS.")
        return ""

    if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
        print(f"{TAG} → ELEVENLABS_API_KEY/VOICE_ID não configurados. Usando fallback.")
        return ""

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": ELEVENLABS_MODEL_ID,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }

    out_path = os.path.join(AUDIO_DIR, f"{news_id}.mp3")

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)

        if resp.status_code != 200:
            print(f"{TAG} → ERRO ElevenLabs (id={news_id}, status={resp.status_code}): {resp.text}")
            return ""

        with open(out_path, "wb") as f:
            f.write(resp.content)

        if os.path.getsize(out_path) == 0:
            print(f"{TAG} → Arquivo de áudio vazio para {news_id}.")
            os.remove(out_path)
            return ""

        print(f"{TAG} → Áudio gerado: {out_path}")
        return out_path

    except requests.exceptions.RequestException as e:
        print(f"{TAG} → ERRO DE CONEXÃO ElevenLabs (id={news_id}):", e)
        return ""


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
    news_id = safe(data.get("id"))
    commentary = safe(data.get("commentary"))

    # Gera o áudio (grava em disco antes de retornar o caminho).
    audio_file = synthesize_audio(commentary, news_id)

    out = {
        "id": news_id,
        "title": safe(data.get("title")),
        "title_original": safe(data.get("title_original")),
        "category": safe(data.get("category")),
        "commentary": commentary,
        "audio_file": safe(audio_file),
        "timestamp": time.time(),
    }

    r.xadd(OUTPUT_STREAM, out)
    # Só confirma depois do áudio gravado em disco e do XADD de saída.
    r.xack(INPUT_STREAM, GROUP, event_id)
    print(f"{TAG} → news.ready:", json.dumps(out, ensure_ascii=False))


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

    print(
        f"{TAG} → recuperação automática ativa "
        f"(timeout={STUCK_TIMEOUT_MS}ms, max_tentativas={MAX_DELIVERY_ATTEMPTS}, "
        f"scan={STUCK_SCAN_INTERVAL_SEC}s).",
        flush=True,
    )

    while True:
        try:
            maybe_reclaim_stuck()

            msgs = r.xreadgroup(GROUP, CONSUMER, {INPUT_STREAM: ">"}, count=10, block=5000)
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
            print(f"{TAG} ERROR:", e, flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
