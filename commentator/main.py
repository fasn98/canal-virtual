import time
import os
import datetime
import redis

import anthropic

from review import (
    review_commentary,
    ReviewUnavailable,
    REVIEW_ENABLED,
    MAX_CORRECTION_ATTEMPTS,
)

r = redis.Redis(host="redis", port=6379, decode_responses=True, socket_timeout=10)

# --- Geração de comentário via Claude (Anthropic) ---
# A chave real vai no .env (ANTHROPIC_API_KEY) — nunca commitada.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
# Modelo padrão. Pode ser trocado sem rebuild via env (ex.: "claude-sonnet-4-5",
# "claude-sonnet-5"). Se o modelo configurado falhar, o fallback curto entra.
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5").strip()
# Tamanho alvo do comentário, em palavras. ~300 palavras ≈ 2 min de fala.
# Reduza (ex.: 40) durante testes para o ciclo passar rápido — sem mudar código.
TARGET_COMMENTARY_WORDS = int(os.environ.get("TARGET_COMMENTARY_WORDS", "300"))
# Timeout (segundos) da chamada à API. Em caso de estouro, cai no fallback.
ANTHROPIC_TIMEOUT_SEC = float(os.environ.get("ANTHROPIC_TIMEOUT_SEC", "60"))
# Workspace da Anthropic. Chaves "identity-linked" (as criadas no Console novo,
# atreladas ao seu login) EXIGEM o header anthropic-workspace-id — sem ele a API
# responde 400. Pegue o id em console.anthropic.com → Settings → Workspaces
# (formato "wrkspc_..."). Em branco = não envia o header (ok para chave antiga
# de workspace único).
ANTHROPIC_WORKSPACE_ID = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()

_anthropic_client = None


def get_anthropic_client():
    """Cliente Anthropic preguiçoso (só cria quando há chave configurada).
    Levanta RuntimeError se a chave não estiver setada — capturado por
    generate_commentary(), que então usa o fallback curto."""
    global _anthropic_client
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY não configurada")
    if _anthropic_client is None:
        headers = {}
        if ANTHROPIC_WORKSPACE_ID:
            headers["anthropic-workspace-id"] = ANTHROPIC_WORKSPACE_ID
        _anthropic_client = anthropic.Anthropic(
            api_key=ANTHROPIC_API_KEY,
            timeout=ANTHROPIC_TIMEOUT_SEC,
            max_retries=2,
            default_headers=headers or None,
        )
    return _anthropic_client

# --- Consumer Group config ---
INPUT_STREAM = "news.classified"
OUTPUT_STREAM = "news.final"
GROUP = "commentator-group"
CONSUMER = os.environ.get("HOSTNAME", "consumer-1")
TAG = "Commentator"

# --- Cache de comentário (por id de notícia) ---
# Hash Redis: campo = id da notícia (estável, vem do collector), valor = texto
# do comentário já gerado pelo Claude. Evita re-chamar a API quando o mesmo id
# volta a passar pelo pipeline — o que acontece bastante, porque o controle de
# duplicatas do collector é em memória e ele re-publica todo o feed a cada
# restart. Sem TTL: comentário de notícia antiga não muda. Só entram no cache
# comentários vindos de fato da API (o fallback curto não é cacheado, para que
# uma passagem futura ainda tenha chance de gerar o comentário real).
COMMENTARY_CACHE_KEY = os.environ.get("COMMENTARY_CACHE_KEY", "commentary_cache")


def get_cached_commentary(news_id):
    """Retorna o comentário já salvo para este id, ou None se não houver
    (ou se o Redis falhar — nesse caso o pipeline apenas gera de novo)."""
    if not news_id:
        return None
    try:
        return r.hget(COMMENTARY_CACHE_KEY, news_id)
    except Exception as e:
        print(f"{TAG} → falha ao ler cache de comentário ({news_id}): {e}", flush=True)
        return None


def save_cached_commentary(news_id, text):
    if not news_id or not text:
        return
    try:
        r.hset(COMMENTARY_CACHE_KEY, news_id, text)
    except Exception as e:
        print(f"{TAG} → falha ao salvar cache de comentário ({news_id}): {e}", flush=True)


# --- Contadores para a metrics-api (best-effort) ---------------------------
# Chaves `metrics:<campo>:<data>` lidas pelo serviço metrics-api (chamadas ao
# Claude por tipo, cache hit/miss de comentário). Qualquer falha é ignorada:
# nunca interfere no pipeline.
METRICS_TTL_SEC = 8 * 24 * 3600


def bump_metric(field, n=1):
    try:
        key = f"metrics:{field}:{datetime.date.today().isoformat()}"
        r.incrby(key, n)
        r.expire(key, METRICS_TTL_SEC)
    except Exception:
        pass

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


FALLBACK_BY_CATEGORY = {
    "Politics": "Esta notícia revela movimentos no cenário político, com possíveis "
               "impactos em decisões governamentais e relações institucionais.",
    "Economy": "O tema envolve fatores econômicos que podem afetar mercados, empregos "
              "e estabilidade financeira, com efeitos no custo de vida.",
    "Security": "Este episódio está ligado à segurança pública e costuma levar a "
               "revisões de protocolos e políticas de segurança.",
    "Climate": "A notícia destaca fenômenos climáticos ou ambientais que reforçam "
              "debates sobre sustentabilidade e preparação das comunidades.",
    "Health": "O assunto envolve saúde e bem-estar, com discussões sobre prevenção, "
             "acesso e políticas sanitárias.",
    "Entertainment": "O foco está em cultura e entretenimento, refletindo tendências "
                    "sociais e comportamentos do público.",
    "Sports": "A notícia envolve o universo esportivo, que mobiliza torcidas, clubes "
             "e movimenta grandes receitas.",
}


def build_fallback_commentary(title, category):
    """Comentário curto e genérico, usado quando a API da Anthropic falha
    (chave inválida, rate limit, timeout) — mantém o pipeline andando."""
    corpo = FALLBACK_BY_CATEGORY.get(
        category,
        "Este acontecimento se insere no cenário internacional, com possíveis "
        "repercussões em política, economia e relações diplomáticas.",
    )
    return (
        f"ANÁLISE: {title}\n"
        f"{corpo}\n"
        "CONCLUSÃO: Seguiremos acompanhando os próximos desdobramentos e trazendo "
        "análises detalhadas conforme novas informações surgirem."
    )


def build_commentary_prompt(title, summary, category, correction=None):
    alvo = TARGET_COMMENTARY_WORDS
    base = (
        "Você é um comentarista de telejornal. Escreva um comentário analítico, "
        "em português do Brasil, sobre a notícia abaixo.\n\n"
        f"TÍTULO: {title}\n"
        f"RESUMO: {summary or '(sem resumo disponível)'}\n"
        f"CATEGORIA: {category}\n\n"
        "Regras:\n"
        f"- Aproximadamente {alvo} palavras (tolerância de ~10%).\n"
        "- Análise jornalística objetiva: contextualize o tema, aponte causas, "
        "implicações e cenários possíveis.\n"
        "- NÃO invente fatos, números, nomes ou declarações que não estejam no "
        "título ou no resumo. Se faltar informação, comente o contexto de forma geral.\n"
        "- É um comentário/interpretação SOBRE a notícia, não a leitura da notícia em si.\n"
        "- Texto corrido, em português, sem título, sem marcadores, sem markdown. "
        "Comece direto no comentário.\n"
    )
    if correction:
        prev_text, motivo = correction
        base += (
            "\nATENÇÃO — esta é uma REESCRITA. A versão anterior foi REPROVADA "
            "na revisão editorial pelo seguinte motivo:\n"
            f"    {motivo}\n"
            "Reescreva o comentário do zero corrigindo especificamente esse "
            "problema, mantendo-se estritamente dentro do título e do resumo "
            "acima. Versão anterior (NÃO repita os erros dela):\n"
            f"---\n{prev_text}\n---\n"
        )
    return base


def generate_commentary(title, summary, category, correction=None):
    """Gera o comentário via Claude (Anthropic). Retorna (texto, veio_da_api):
    veio_da_api=True quando a resposta é do Claude (pode ser cacheada),
    False quando qualquer erro de API (chave inválida, rate limit, timeout,
    resposta vazia) forçou o fallback curto — que NÃO deve ser cacheado.
    `correction=(texto_anterior, motivo)` pede uma reescrita corrigida."""
    try:
        client = get_anthropic_client()
        prompt = build_commentary_prompt(title, summary, category, correction)
        # ~3.2 tokens por palavra-alvo, com piso e teto de segurança.
        max_toks = min(2000, max(400, int(TARGET_COMMENTARY_WORDS * 3.2)))

        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_toks,
            messages=[{"role": "user", "content": prompt}],
        )
        bump_metric("claude_calls:commentary")

        text = "\n".join(
            b.text.strip() for b in resp.content
            if getattr(b, "type", None) == "text" and b.text.strip()
        ).strip()

        if not text:
            raise RuntimeError("resposta vazia da API")

        print(
            f"{TAG} → comentário Claude OK "
            f"(modelo={ANTHROPIC_MODEL}, ~{len(text.split())} palavras)",
            flush=True,
        )
        return text, True

    except Exception as e:
        print(
            f"{TAG} → ERRO na API Anthropic ({type(e).__name__}: {e}). "
            "Usando fallback curto.",
            flush=True,
        )
        return build_fallback_commentary(title, category), False


def produce_approved_commentary(news_id, title, title_original, summary,
                                category, source):
    """Gera o comentário e o submete aos três revisores (verificador de fatos,
    revisor editorial, aprovador final). Enquanto BLOQUEADO, devolve ao
    commentator para reescrever com o motivo da rejeição — no máximo
    MAX_CORRECTION_ATTEMPTS correções. Retorna o texto LIBERADO, ou None se as
    tentativas se esgotaram (o item deve ser descartado, não publicado).
    Levanta ReviewUnavailable se a revisão não pôde ser feita (API fora)."""
    client = get_anthropic_client()

    prev_text, motivo = None, None
    for tentativa in range(MAX_CORRECTION_ATTEMPTS + 1):
        if tentativa == 0:
            text, from_api = generate_commentary(title, summary, category)
        else:
            print(
                f"{TAG} → Devolvido ao commentator (correção {tentativa}/"
                f"{MAX_CORRECTION_ATTEMPTS}) para {news_id}: {motivo}",
                flush=True,
            )
            text, from_api = generate_commentary(
                title, summary, category, correction=(prev_text, motivo)
            )

        # Fallback curto = API do comentário fora; os revisores usam a mesma
        # API e também vão falhar. Não adianta revisar: deixa a mensagem
        # pendente para o reprocessamento tentar de novo mais tarde.
        if not from_api:
            raise ReviewUnavailable("API de geração indisponível (fallback curto)")

        liberado, motivo = review_commentary(
            client=client,
            news_id=news_id,
            commentary=text,
            title=title,
            title_original=title_original,
            summary=summary,
            category=category,
            source=source,
        )
        if liberado:
            return text
        prev_text = text

    return None


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
    summary = safe(data.get("summary", ""))
    # Rótulo da fonte ("BBC"/"Guardian"), vindo do collector via classifier.
    source = safe(data.get("source", ""))

    # Cache por id: guarda apenas o comentário JÁ APROVADO. Um HIT pula tanto
    # a geração quanto a revisão — uma notícia aprovada não é revista de novo.
    commentary = get_cached_commentary(news_id)
    if commentary:
        print(
            f"{TAG} → Cache HIT para {news_id}, reaproveitando comentário aprovado",
            flush=True,
        )
        bump_metric("cache:commentary:hit")
    elif REVIEW_ENABLED:
        bump_metric("cache:commentary:miss")
        commentary = produce_approved_commentary(
            news_id, title, title_original, summary, category, source
        )
        if commentary is None:
            # Reprovado nas tentativas de correção: NÃO publica. Descarta este
            # item (XACK sem XADD) e segue para o próximo da fila.
            r.xack(INPUT_STREAM, GROUP, event_id)
            print(
                f"{TAG} → ITEM DESCARTADO para {news_id}: reprovado na revisão "
                f"editorial após {MAX_CORRECTION_ATTEMPTS} tentativas de "
                f"correção. NÃO publicado. title={title!r}",
                flush=True,
            )
            return
        save_cached_commentary(news_id, commentary)
    else:
        # Revisão desligada (ENABLE_EDITORIAL_REVIEW=false): comportamento
        # antigo — publica direto, cacheia só o que veio da API.
        bump_metric("cache:commentary:miss")
        commentary, from_api = generate_commentary(title, summary, category)
        if from_api:
            save_cached_commentary(news_id, commentary)

    out = {
        "id": news_id,
        "title": title,
        "title_original": title_original,
        "category": category,
        "commentary": commentary,
        "source": source,
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
    if REVIEW_ENABLED:
        print(
            f"{TAG} → revisão editorial ATIVA (verificador de fatos + revisor "
            f"editorial + aprovador final; até {MAX_CORRECTION_ATTEMPTS} "
            f"correções, depois descarta).",
            flush=True,
        )
    else:
        print(f"{TAG} → revisão editorial DESLIGADA (ENABLE_EDITORIAL_REVIEW=false).", flush=True)

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
