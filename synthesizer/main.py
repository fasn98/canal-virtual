import time
import os
import re
import datetime
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

# --- Freio de gasto diário na ElevenLabs -------------------------------------
# Teto de créditos/dia para a síntese de voz de NOVAS notícias, INDEPENDENTE de
# quantas fontes de notícia estejam ativas. Plano Creator = 121.038 créditos/mês
# / 30 ≈ 4.034/dia; o padrão 4000 embute uma margem de segurança. No modelo
# multilingual v2 (o que usamos) 1 caractere enviado ≈ 1 crédito.
DAILY_CREDIT_BUDGET = int(os.environ.get("DAILY_CREDIT_BUDGET", "4000"))

# Contador de gasto do dia no Redis. A chave já muda de nome por data, então o
# reset diário é natural; o TTL só evita que chaves de dias antigos acumulem
# para sempre.
CREDITS_USED_KEY_PREFIX = "elevenlabs:credits_used:"
CREDITS_KEY_TTL_SEC = 7 * 24 * 3600


def _today_key():
    return CREDITS_USED_KEY_PREFIX + datetime.date.today().isoformat()


def get_credits_used_today():
    """Créditos ElevenLabs já gastos hoje (0 se a chave não existe ou o Redis
    falhou — nesse caso preferimos deixar passar a travar o canal)."""
    try:
        v = r.get(_today_key())
        return int(v) if v else 0
    except Exception as e:
        print(f"{TAG} → AVISO: falha ao ler contador de créditos ({e}); assumindo 0.", flush=True)
        return 0


def add_credits_used(chars):
    """Soma `chars` ao contador do dia e renova o TTL. Retorna o novo total
    (ou None em falha)."""
    try:
        key = _today_key()
        total = r.incrby(key, chars)
        r.expire(key, CREDITS_KEY_TTL_SEC)
        return total
    except Exception as e:
        print(f"{TAG} → AVISO: falha ao atualizar contador de créditos ({e}).", flush=True)
        return None


def log_budget(used):
    remaining = DAILY_CREDIT_BUDGET - used
    print(
        f"{TAG} → Orçamento diário: {used}/{DAILY_CREDIT_BUDGET} créditos "
        f"({remaining} restantes)",
        flush=True,
    )


# --- Contadores para a metrics-api (best-effort) ---------------------------
# Chaves `metrics:<campo>:<data>` lidas pelo serviço metrics-api. Nunca
# interferem no fluxo: qualquer falha de Redis é engolida.
METRICS_TTL_SEC = 8 * 24 * 3600


def bump_metric(field, n=1):
    try:
        key = f"metrics:{field}:{datetime.date.today().isoformat()}"
        r.incrby(key, n)
        r.expire(key, METRICS_TTL_SEC)
    except Exception:
        pass


def safe(v):
    if v is None:
        return ""
    return str(v)


# --- Detecção de itens de teste -------------------------------------------
# Espelha renderer.is_test_item: id de produção = sha256(...)[:16] (16 chars
# hex) ou "promo-...". Qualquer outro id (ou manchete "TESTE ...") é injeção
# manual de teste — não deve entrar no índice de reprises nem ser reprisado,
# senão um teste fica rodando em rotação quando o pipeline real está parado.
_HEX16_RE = re.compile(r"^[0-9a-f]{16}$")
_TEST_ID_PREFIXES = (
    "test", "teste", "skiptest", "lipsynctest", "cache-test", "cachetest",
    "cc-test", "cctest", "cg-test", "cgtest", "budgettest", "budget-test",
    "reprisetest", "estudioteste", "dummy", "kill",
)


def is_test_id(news_id, title=""):
    nid = (news_id or "").strip().lower()
    if nid.startswith("promo-"):
        return False
    if not _HEX16_RE.match(nid) or nid.startswith(_TEST_ID_PREFIXES):
        return True
    t = " ".join((title or "").split()).lower()
    return t == "teste" or t.startswith(
        ("teste ", "teste:", "teste-", "test ", "test:", "test-")
    )


# --- Índice de notícias "completas" de hoje (para reprises sem custo) --------
# Toda notícia que JÁ tem áudio real pago (gerado agora OU cache HIT) entra num
# Hash Redis por data: field = id, value = JSON com o que o renderer precisa
# para remontar o bloco (título, categoria, comentário, caminho do áudio).
# Quando o orçamento do dia esgota, em vez de simplesmente pular, o synthesizer
# republica em ROTAÇÃO uma dessas notícias em news.ready — o renderer remonta o
# vídeo com ffmpeg local, sem tocar na ElevenLabs nem no D-ID (o audio_file já
# existe e cai no cache). A chave muda de nome por data (reset diário natural)
# e tem TTL curto só para não acumular chaves de dias antigos.
COMPLETED_ITEMS_KEY_PREFIX = "today:completed_items:"
COMPLETED_ITEMS_TTL_SEC = 2 * 24 * 3600
# Ponteiro de rotação (round-robin) das reprises, também por data.
REPRISE_POS_KEY_PREFIX = "synthesizer:reprise_pos:"


def _completed_key():
    return COMPLETED_ITEMS_KEY_PREFIX + datetime.date.today().isoformat()


def record_completed_item(item):
    """Indexa uma notícia que já tem áudio real pago, para poder reprisá-la de
    graça mais tarde (ver pick_reprise_item). Best-effort: qualquer falha de
    Redis é só logada, nunca interrompe o fluxo."""
    news_id = safe(item.get("id"))
    if not news_id:
        return
    if is_test_id(news_id, safe(item.get("title"))):
        print(
            f"{TAG} → item de teste {news_id!r} NÃO entra no índice de reprises "
            f"(não deve ir ao ar em rotação).",
            flush=True,
        )
        return
    try:
        payload = json.dumps(
            {
                "id": news_id,
                "title": safe(item.get("title")),
                "title_original": safe(item.get("title_original")),
                "category": safe(item.get("category")),
                "commentary": safe(item.get("commentary")),
                "source": safe(item.get("source")),
                "audio_file": safe(item.get("audio_file")),
            },
            ensure_ascii=False,
        )
        key = _completed_key()
        r.hset(key, news_id, payload)
        r.expire(key, COMPLETED_ITEMS_TTL_SEC)
    except Exception as e:
        print(f"{TAG} → AVISO: falha ao indexar notícia completa {news_id} ({e}).", flush=True)


def pick_reprise_item():
    """Escolhe, em rotação simples (round-robin sobre os ids ordenados), uma
    notícia já paga de hoje para reprisar sem gastar crédito novo. Retorna o
    dict do item ou None se ainda não há NENHUMA notícia completa hoje (cenário
    raro, bem cedo no dia). Com 2+ itens no índice, a rotação nunca repete o
    mesmo item duas vezes seguidas."""
    try:
        key = _completed_key()
        # Filtra qualquer id de teste que tenha entrado no índice antes desta
        # proteção existir — um teste nunca deve ser reprisado ao ar.
        ids = sorted(k for k in r.hkeys(key) if not is_test_id(k))
    except Exception as e:
        print(f"{TAG} → AVISO: falha ao ler índice de notícias completas ({e}).", flush=True)
        return None
    if not ids:
        return None
    try:
        pos_key = REPRISE_POS_KEY_PREFIX + datetime.date.today().isoformat()
        pos = r.incr(pos_key)
        r.expire(pos_key, COMPLETED_ITEMS_TTL_SEC)
    except Exception:
        pos = 0
    chosen_id = ids[pos % len(ids)]
    try:
        raw = r.hget(key, chosen_id)
        return json.loads(raw) if raw else None
    except Exception as e:
        print(f"{TAG} → AVISO: falha ao carregar notícia {chosen_id} para reprise ({e}).", flush=True)
        return None


def synthesize_audio(text, news_id, is_promo=False):
    """
    Gera o áudio real da notícia via ElevenLabs TTS.
    Retorna uma tupla (audio_path, budget_exceeded):
      - audio_path  = caminho do mp3 gerado (síntese nova OU cache HIT), ou ""
        quando NÃO há áudio real: TTS falhou (sem crédito/quota_exceeded, chave
        inválida, HTTP != 200, erro de rede, arquivo vazio) ou o texto veio
        vazio. Nesse caso handle_event NÃO publica a notícia em news.ready — sem
        áudio real não vai ao ar bloco novo, e o streamer segue transmitindo o
        último final.mp4 completo (ver handle_event).
      - budget_exceeded = True quando NÃO chamamos a ElevenLabs porque o
        orçamento diário de créditos acabaria — caso à parte, tratado com
        reprise/skip em handle_event (não confundir com falha de síntese).
    """
    if not text.strip():
        print(f"{TAG} → Texto vazio para {news_id}, pulando TTS.")
        return "", False

    out_path = os.path.join(AUDIO_DIR, f"{news_id}.mp3")

    # Cache por id: se já existe um mp3 não-vazio para esta notícia, reusa
    # direto sem chamar a ElevenLabs (economia de créditos). O id vem do
    # collector e é estável; áudio de notícia antiga não muda, então sem TTL.
    # Cobre o re-processamento do mesmo id quando o collector re-publica o
    # feed após restart (dedupe dele é só em memória). Cache não gasta
    # orçamento nem sofre o freio.
    if news_id and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        print(
            f"{TAG} → Cache HIT para {news_id}, reaproveitando audio existente",
            flush=True,
        )
        bump_metric("cache:audio:hit")
        return out_path, False

    # Passou do cache: vai ser preciso sintetizar (ou barrar pelo freio) — conta
    # como MISS para a taxa de cache do painel.
    if news_id:
        bump_metric("cache:audio:miss")

    if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
        print(f"{TAG} → ELEVENLABS_API_KEY/VOICE_ID não configurados. Usando fallback.")
        return "", False

    # --- Freio de gasto diário --------------------------------------------
    # 1 caractere ≈ 1 crédito (multilingual v2). Antes de CADA chamada, checa
    # se (uso de hoje + tamanho deste texto) passaria do teto. Se passaria,
    # nem chamamos a API — poupamos o round-trip de uma chamada que sabemos
    # que falharia por cota, e sinalizamos budget_exceeded para o renderer.
    # Blocos promocionais NÃO entram no freio: a chamada do canal irmão não
    # pode sumir do ar por causa da cota (e o id fixo dela já bate no cache a
    # partir da 2ª vez). O gasto real dela ainda é contabilizado no total.
    char_cost = len(text)
    if not is_promo:
        used = get_credits_used_today()
        if used + char_cost > DAILY_CREDIT_BUDGET:
            print(
                f"{TAG} → ORÇAMENTO DIÁRIO ESGOTADO: {used} usados + {char_cost} desta "
                f"chamada passaria de {DAILY_CREDIT_BUDGET}. NÃO vou chamar a ElevenLabs "
                f"para {news_id}.",
                flush=True,
            )
            log_budget(used)
            return "", True

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

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)

        if resp.status_code != 200:
            print(f"{TAG} → ERRO ElevenLabs (id={news_id}, status={resp.status_code}): {resp.text}")
            return "", False

        with open(out_path, "wb") as f:
            f.write(resp.content)

        if os.path.getsize(out_path) == 0:
            print(f"{TAG} → Arquivo de áudio vazio para {news_id}.")
            os.remove(out_path)
            return "", False

        # Só contabiliza o gasto depois de confirmar mp3 válido em disco
        # ("cada chamada bem-sucedida"). INCRBY é atômico; concorrência entre
        # múltiplos consumers pode estourar o teto por no máx. ~1 chamada.
        total = add_credits_used(char_cost)
        log_budget(total if total is not None else get_credits_used_today())

        print(f"{TAG} → Áudio gerado: {out_path}")
        return out_path, False

    except requests.exceptions.RequestException as e:
        print(f"{TAG} → ERRO DE CONEXÃO ElevenLabs (id={news_id}):", e)
        return "", False


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
    category = safe(data.get("category"))
    is_promo = category.strip().lower() in ("promoção", "promocao", "promo")

    # Gera o áudio (grava em disco antes de retornar o caminho). budget_exceeded
    # = True quando o freio de gasto diário barrou a chamada; nesse caso o
    # renderer pula o item e mantém o último bloco bom no ar.
    audio_file, budget_exceeded = synthesize_audio(commentary, news_id, is_promo=is_promo)

    # --- Falha de síntese (ElevenLabs indisponível / sem crédito / quota_exceeded
    # / chave inválida / erro de rede / texto vazio): NÃO publica a notícia em
    # news.ready. Publicar com audio_file="" faria o renderer gerar um final.mp4
    # NOVO com a MANCHETE NOVA e o áudio dummy antigo ("nenhuma notícia
    # disponível") — combinação incoerente que já foi ao ar. Em vez disso:
    # loga o skip, dá XACK (a mensagem NÃO volta pra fila — enquanto a cota
    # estiver zerada ela falharia de novo) e segue. Como nada chega em
    # news.ready, o renderer não gera bloco novo e o streamer continua no
    # ÚLTIMO final.mp4 completo. Quando a síntese voltar a funcionar (ou cair no
    # cache), o ciclo normaliza sozinho. É mutuamente exclusivo com
    # budget_exceeded, tratado logo abaixo; o fallback do D-ID no renderer (áudio
    # real sem animação labial) NÃO é afetado.
    if not budget_exceeded and not audio_file:
        print(
            f"{TAG} → PULADO {news_id}: falha na síntese de áudio, sem crédito/erro. "
            f"Notícia não publicada.",
            flush=True,
        )
        bump_metric("synth:skipped")
        r.xack(INPUT_STREAM, GROUP, event_id)
        return

    # --- Orçamento esgotado: em vez de só pular, reprisa em rotação uma notícia
    # de hoje que JÁ tem áudio real pago (republica em news.ready sem novo gasto
    # de ElevenLabs/D-ID — o audio_file já existe e o renderer cai no cache).
    # Só se ainda não houver NENHUMA notícia completa hoje é que caímos no
    # comportamento antigo (marcar budget_exceeded e o renderer pular, mantendo
    # o bloco atual no ar). -----------------------------------------------------
    if budget_exceeded:
        reprise = pick_reprise_item()
        if reprise:
            out = {
                "id": safe(reprise.get("id")),
                "title": safe(reprise.get("title")),
                "title_original": safe(reprise.get("title_original")),
                "category": safe(reprise.get("category")),
                "commentary": safe(reprise.get("commentary")),
                "source": safe(reprise.get("source")),
                "audio_file": safe(reprise.get("audio_file")),
                "budget_exceeded": "false",
                "reprise": "true",
                "timestamp": time.time(),
            }
            r.xadd(OUTPUT_STREAM, out)
            r.xack(INPUT_STREAM, GROUP, event_id)
            bump_metric("reprise")
            print(
                f"{TAG} → Orçamento esgotado, reprisando notícia já paga: "
                f"{out['id']} ({out['title']})",
                flush=True,
            )
            print(f"{TAG} → news.ready:", json.dumps(out, ensure_ascii=False))
            return
        print(
            f"{TAG} → Orçamento esgotado e nenhuma notícia paga hoje ainda; "
            f"pulando {news_id} e mantendo o bloco atual no ar.",
            flush=True,
        )

    out = {
        "id": news_id,
        "title": safe(data.get("title")),
        "title_original": safe(data.get("title_original")),
        "category": category,
        "commentary": commentary,
        "source": safe(data.get("source")),
        "audio_file": safe(audio_file),
        "budget_exceeded": "true" if budget_exceeded else "false",
        "timestamp": time.time(),
    }

    r.xadd(OUTPUT_STREAM, out)
    # Só confirma depois do áudio gravado em disco e do XADD de saída.
    r.xack(INPUT_STREAM, GROUP, event_id)
    print(f"{TAG} → news.ready:", json.dumps(out, ensure_ascii=False))

    # Notícia real com áudio pago (gerado agora ou cache HIT): entra no índice
    # de "completas de hoje" para poder ser reprisada de graça quando o
    # orçamento esgotar. Promo e itens sem áudio real ficam de fora.
    if not is_promo and not budget_exceeded and safe(audio_file):
        record_completed_item(out)


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
