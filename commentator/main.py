import time
import os
import redis
import json

r = redis.Redis(host="redis", port=6379, decode_responses=True, socket_timeout=10)

# --- Consumer Group config ---
INPUT_STREAM = "news.classified"
OUTPUT_STREAM = "news.final"
GROUP = "commentator-group"
CONSUMER = os.environ.get("HOSTNAME", "consumer-1")
TAG = "Commentator"

# --- Recuperação automática de mensagens travadas ---
STUCK_TIMEOUT_MS = int(os.environ.get("STUCK_MESSAGE_TIMEOUT_MS", "60000"))
MAX_DELIVERY_ATTEMPTS = int(os.environ.get("MAX_DELIVERY_ATTEMPTS", "3"))
STUCK_SCAN_INTERVAL_SEC = int(os.environ.get("STUCK_SCAN_INTERVAL_SEC", "30"))

_last_stuck_scan = 0.0


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


def safe(v):
    if v is None:
        return ""
    return str(v)


def build_commentary(title, summary, category, script):
    commentary = []

    commentary.append(f"ANÁLISE: {title}")

    if category == "Politics":
        commentary.append(
            "Esta notícia revela movimentos importantes no cenário político, "
            "com possíveis impactos em decisões governamentais e relações institucionais."
        )
        commentary.append(
            "Historicamente, eventos desse tipo influenciam debates públicos e moldam a percepção da população."
        )

    elif category == "Economy":
        commentary.append(
            "O tema envolve fatores econômicos que podem afetar mercados, empregos e estabilidade financeira."
        )
        commentary.append(
            "Mudanças econômicas costumam gerar efeitos imediatos no custo de vida e nas políticas fiscais."
        )

    elif category == "Security":
        commentary.append(
            "Este episódio está ligado à segurança pública e pode gerar preocupação social significativa."
        )
        commentary.append(
            "Incidentes desse tipo frequentemente levam a revisões de protocolos e políticas de segurança."
        )

    elif category == "Climate":
        commentary.append(
            "A notícia destaca fenômenos climáticos ou ambientais que podem ter efeitos duradouros."
        )
        commentary.append(
            "Eventos climáticos extremos reforçam debates sobre sustentabilidade e preparação de comunidades."
        )

    elif category == "Health":
        commentary.append(
            "O assunto envolve saúde e bem-estar, temas que afetam diretamente a vida cotidiana."
        )
        commentary.append(
            "Questões de saúde pública costumam gerar discussões sobre prevenção, acesso e políticas sanitárias."
        )

    elif category == "Entertainment":
        commentary.append(
            "O foco está em cultura e entretenimento, refletindo tendências sociais e comportamentos do público."
        )
        commentary.append(
            "Produções culturais frequentemente influenciam debates sociais e moldam identidades coletivas."
        )

    elif category == "Sports":
        commentary.append(
            "A notícia envolve o universo esportivo, que mobiliza torcidas e movimenta grandes receitas."
        )
        commentary.append(
            "Eventos esportivos têm impacto direto em comunidades, clubes e na identidade cultural."
        )

    else:
        commentary.append(
            "Este acontecimento se insere no cenário internacional, com possíveis repercussões em diferentes regiões."
        )
        commentary.append(
            "Eventos globais costumam influenciar política, economia e relações diplomáticas."
        )

    commentary.append(
        "CONCLUSÃO: Seguiremos acompanhando os próximos desdobramentos e trazendo análises detalhadas conforme novas informações surgirem."
    )

    return "\n".join(commentary)


def handle_event(event_id, data):
    """Processa UMA mensagem. Usado tanto pelo XREADGROUP (mensagens novas)
    quanto pelo XAUTOCLAIM (mensagens travadas recuperadas). Levanta exceção
    em caso de falha — nesse caso NÃO há XACK e a mensagem segue pendente."""
    news_id = safe(data.get("id"))
    title = safe(data.get("title"))
    # Título já traduzido pelo classifier. `title_original` (inglês) segue
    # adiante só para rastreabilidade.
    title_original = safe(data.get("title_original"))
    category = safe(data.get("category"))
    script = safe(data.get("script"))
    summary = safe(data.get("summary", ""))

    commentary = build_commentary(title, summary, category, script)

    out = {
        "id": news_id,
        "title": title,
        "title_original": title_original,
        "category": category,
        "commentary": commentary,
        "timestamp": safe(time.time()),
    }

    r.xadd(OUTPUT_STREAM, out)
    # Só confirma depois que a mensagem de saída foi publicada.
    r.xack(INPUT_STREAM, GROUP, event_id)
    print(f"{TAG} → {category}: {title}")


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
