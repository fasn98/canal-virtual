import os
import time

import redis

r = redis.Redis(host="redis", port=6379, decode_responses=True, socket_timeout=10)

# Gatilho orientado a evento (não mais timer): o promoter ESCUTA news.block
# — a saída do renderer, onde cada notícia renderizada com sucesso vira uma
# mensagem — e conta as notícias REAIS. A cada N reais, injeta 1 bloco
# promocional em news.final. A partir daí a mensagem segue o pipeline normal
# (synthesizer -> renderer -> streamer), sem lógica de geração duplicada.
INPUT_STREAM = "news.block"
OUTPUT_STREAM = "news.final"
GROUP = "promoter-group"
CONSUMER = os.environ.get("HOSTNAME", "promoter-1")
TAG = "Promoter"

# Contador de notícias reais desde a última promoção. Chave no Redis para
# sobreviver a restart do container.
COUNTER_KEY = "promo:real_news_count"
# Notícias reais por ciclo antes de disparar a promoção.
# Produção = 5 (5x ~2min + 1 promo ≈ ciclo de ~11 min).
NEWS_PER_PROMO = int(os.environ.get("NEWS_PER_PROMO", "5"))

# ID FIXO da chamada promocional. Como o texto (áudio) nunca muda entre
# execuções, um id estável faz o synthesizer (mp3 em disco) e o lip-sync
# (mp4 D-ID) baterem no cache a partir da 2ª vez — 0 crédito ElevenLabs /
# D-ID por promoção. Para forçar UMA regeração (mudou o roteiro), suba o
# sufixo de versão: v1 -> v2 -> v3 ... O id novo é cache miss uma única vez
# e depois volta a ser reaproveitado para sempre.
PROMO_ID = "promo-futureverse-beyond-v3"
PROMO_TITLE = "Inscreva-se: FutureVerse & Beyond"
PROMO_CATEGORY = "Promoção"
PROMO_COMMENTARY = (
    "Se você gosta do que vê aqui, vai adorar o canal FutureVerse & Beyond. "
    "São mais de três mil vídeos sobre ciência, tecnologia, astronomia, aviação, "
    "geopolítica e além. Inscreva-se, deixe seu like, e faça parte dessa comunidade "
    "que já é referência em conteúdo de qualidade. E fica de olho no nosso chat ao "
    "vivo: a cada bloco a gente solta uma pergunta pra você responder e participar "
    "das enquetes e quizzes com a gente, ao vivo. O link está na tela."
)


def is_promo_category(cat):
    return (cat or "").strip().lower() in ("promoção", "promocao", "promo")


def ensure_group():
    """Cria o consumer group na inicialização. BUSYGROUP (grupo já existe)
    é esperado em restarts e não é um erro real."""
    try:
        r.xgroup_create(INPUT_STREAM, GROUP, id="$", mkstream=True)
        print(f"{TAG} → consumer group '{GROUP}' criado em '{INPUT_STREAM}'.", flush=True)
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP" in str(e):
            print(f"{TAG} → consumer group '{GROUP}' já existe. OK.", flush=True)
        else:
            raise


def publish_promo():
    now = time.time()
    promo = {
        "id": PROMO_ID,
        "title": PROMO_TITLE,
        "title_original": "",
        "category": PROMO_CATEGORY,
        "commentary": PROMO_COMMENTARY,
        "timestamp": now,
    }
    r.xadd(OUTPUT_STREAM, promo)
    print(f"{TAG} → chamada promocional publicada em {OUTPUT_STREAM}: {promo['id']}", flush=True)


def handle_event(event_id, data):
    """Conta 1 notícia real; ao atingir NEWS_PER_PROMO dispara a promoção e
    zera o contador. Blocos promocionais que passam pelo pipeline não contam."""
    category = data.get("category", "")

    if is_promo_category(category):
        print(f"{TAG} → bloco promocional passou pelo pipeline; não conta.", flush=True)
        r.xack(INPUT_STREAM, GROUP, event_id)
        return

    count = r.incr(COUNTER_KEY)
    title = data.get("title", "?")
    print(f"{TAG} → notícia real renderizada: {title!r} — contador {count}/{NEWS_PER_PROMO}", flush=True)

    if count >= NEWS_PER_PROMO:
        publish_promo()
        r.set(COUNTER_KEY, 0)
        print(f"{TAG} → contador zerado; novo ciclo começa agora.", flush=True)

        # Bot de engajamento: 1 pergunta no chat ao vivo por BLOCO, sobre a
        # notícia que acabou de fechar o ciclo (a mais recente — "a notícia do
        # momento"). Best-effort e isolado: maybe_post_block_question captura
        # qualquer erro internamente e NUNCA quebra o pipeline promocional.
        try:
            from engage import maybe_post_block_question

            maybe_post_block_question(
                r,
                news_id=data.get("id", ""),
                title=title,
                category=category,
                summary=data.get("text", ""),
            )
        except Exception as e:
            print(
                f"{TAG} → engage indisponível ({type(e).__name__}: {e}); segue.",
                flush=True,
            )

    # XACK só depois de contar/disparar. Se o container morrer entre o INCR e o
    # XACK, a mensagem é reentregue e contada de novo — impacto pequeno (a promo
    # dispara no máximo uma notícia adiantada/atrasada), aceitável aqui.
    r.xack(INPUT_STREAM, GROUP, event_id)


def main():
    while True:
        try:
            ensure_group()
            break
        except Exception as e:
            print(f"{TAG} → falha ao criar consumer group, tentando de novo:", e, flush=True)
            time.sleep(3)

    print(
        f"{TAG} → online. Escutando '{INPUT_STREAM}', 1 promoção a cada "
        f"{NEWS_PER_PROMO} notícias reais. Contador atual: "
        f"{r.get(COUNTER_KEY) or 0}.",
        flush=True,
    )
    try:
        from engage import ENABLED as ENGAGE_ENABLED

        print(
            f"{TAG} → bot de engajamento no chat ao vivo: "
            f"{'ATIVADO' if ENGAGE_ENABLED else 'desativado'} "
            f"(ENABLE_ENGAGEMENT_BOT). 1 pergunta por bloco.",
            flush=True,
        )
    except Exception as e:
        print(f"{TAG} → módulo engage não carregou ({type(e).__name__}: {e}).", flush=True)

    while True:
        try:
            msgs = r.xreadgroup(GROUP, CONSUMER, {INPUT_STREAM: ">"}, count=10, block=5000)
            if not msgs:
                continue
            for _stream, events in msgs:
                for event_id, data in events:
                    try:
                        handle_event(event_id, data)
                    except Exception as e:
                        print(f"{TAG} → ERRO ao processar {event_id} (fica pendente):", e, flush=True)
        except Exception as e:
            print(f"{TAG} ERROR:", e, flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
