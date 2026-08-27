import time
import os
import redis
import json

from translate import translate_text, TARGET_LANGUAGE

r = redis.Redis(host="redis", port=6379, decode_responses=True, socket_timeout=10)

# --- Consumer Group config ---
INPUT_STREAM = "news.raw"
OUTPUT_STREAM = "news.classified"
GROUP = "classifier-group"
CONSUMER = os.environ.get("HOSTNAME", "consumer-1")
TAG = "Classifier"

# --- Recuperação automática de mensagens travadas ---
STUCK_TIMEOUT_MS = int(os.environ.get("STUCK_MESSAGE_TIMEOUT_MS", "60000"))
MAX_DELIVERY_ATTEMPTS = int(os.environ.get("MAX_DELIVERY_ATTEMPTS", "3"))
STUCK_SCAN_INTERVAL_SEC = int(os.environ.get("STUCK_SCAN_INTERVAL_SEC", "30"))

_last_stuck_scan = 0.0

CATEGORIES = {
    "Politics": ["election", "president", "government", "minister", "parliament", "policy", "macron", "biden", "trump"],
    "Economy": ["inflation", "market", "economy", "trade", "finance", "stocks", "bank"],
    "Technology": ["ai", "tech", "software", "hardware", "robot", "cyber", "data"],
    "Health": ["health", "virus", "covid", "hospital", "disease", "medical"],
    "Science": ["research", "scientist", "study", "space", "nasa"],
    "Climate": ["climate", "heatwave", "wildfire", "flood", "drought", "environment"],
    "Security": ["shooting", "attack", "police", "crime", "military", "strike"],
    "Entertainment": ["film", "movie", "actor", "actress", "music", "netflix"],
    "Sports": ["football", "soccer", "nba", "fifa", "olympics"],
    "Lifestyle": ["travel", "fashion", "culture", "food"]
}


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


def classify(text):
    text_lower = text.lower()
    scores = {}

    for category, keywords in CATEGORIES.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[category] = score

    if not scores:
        return "World", 0.1

    best_category = max(scores, key=scores.get)
    confidence = scores[best_category] / (len(CATEGORIES[best_category]) + 1)

    return best_category, round(confidence, 2)


def handle_event(event_id, data):
    """Processa UMA mensagem. Usado tanto pelo XREADGROUP (mensagens novas)
    quanto pelo XAUTOCLAIM (mensagens travadas recuperadas). Levanta exceção
    em caso de falha — nesse caso NÃO há XACK e a mensagem segue pendente."""
    # Título como veio do collector (BBC, em inglês). A classificação por
    # palavras-chave abaixo depende do texto EM INGLÊS, então roda antes da
    # tradução.
    title_original = data.get("title", "")
    summary = data.get("summary", "")
    text = f"{title_original} {summary}".strip()

    category, confidence = classify(text)

    # A partir daqui o pipeline trabalha só com o título já traduzido para o
    # idioma-alvo do canal. `title_original` segue adiante apenas para
    # rastreabilidade/debug. Se a DeepL falhar, translate_text() devolve o
    # título original e o pipeline segue (degradação graciosa).
    title = translate_text(title_original, TARGET_LANGUAGE)

    msg = {
        "id": data.get("id", ""),
        "title": title,
        "title_original": title_original,
        "summary": summary,
        "category": category,
        "confidence": confidence,
        "link": data.get("link", ""),
        "published": data.get("published", ""),
        "source": data.get("source", ""),
        "timestamp": time.time(),
    }

    r.xadd(OUTPUT_STREAM, msg)
    # Só confirma depois que a mensagem de saída foi publicada.
    r.xack(INPUT_STREAM, GROUP, event_id)
    print(f"{TAG} → news.classified:", json.dumps(msg, ensure_ascii=False))


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
            time.sleep(5)


if __name__ == "__main__":
    main()
