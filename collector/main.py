import time
import os
import redis
import feedparser
import hashlib
import html
import json
import re

import requests

r = redis.Redis(host="redis", port=6379, decode_responses=True)

TAG = "Collector"

# ---------------------------------------------------------------------------
# Fontes de notícia
# ---------------------------------------------------------------------------
# Cada item coletado carrega um rótulo de fonte estável ("BBC", "Guardian")
# no campo `source` — usado pelo verificador de fatos (serviço commentator) e,
# no futuro, para dar crédito visual à fonte na tela. O `link`/URL real da
# matéria continua em `link`.
#
# Dedup: continua sendo só em memória (`seen_ids`), por id. O id é derivado do
# link/URL da matéria, que é único por fonte, então o mesmo item da MESMA fonte
# não é republicado dentro do processo. Dedup semântico entre fontes diferentes
# (mesma notícia, títulos diferentes) NÃO é tratado aqui — fica para uma fase
# futura.

# --- BBC: RSS (mesma fonte já validada, só mais categorias) ---
# URLs oficiais dos feeds do BBC News (feeds.bbci.co.uk). Sobrescrevível via
# env BBC_FEED_URLS (lista separada por vírgula).
DEFAULT_BBC_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "https://feeds.bbci.co.uk/news/health/rss.xml",
]
BBC_FEED_URLS = [
    u.strip()
    for u in (os.environ.get("BBC_FEED_URLS") or ",".join(DEFAULT_BBC_FEEDS)).split(",")
    if u.strip()
]

# --- The Guardian: Open Platform (API oficial documentada) ---
# https://open-platform.theguardian.com/documentation/
# Endpoint de busca de conteúdo. Auth por api-key (query param). O tier
# "developer" (o da nossa chave) permite 5.000 chamadas/dia, 12/s, uso
# não-comercial, com atribuição e link de volta para theguardian.com.
GUARDIAN_API_KEY = os.environ.get("GUARDIAN_API_KEY", "").strip()
GUARDIAN_ENDPOINT = "https://content.guardianapis.com/search"
# Seções consultadas (uma chamada por seção). Espelham as categorias da BBC.
GUARDIAN_SECTIONS = [
    s.strip()
    for s in (
        os.environ.get("GUARDIAN_SECTIONS")
        or "world,business,technology,science,environment,society"
    ).split(",")
    if s.strip()
]
# 1 chamada por seção por ciclo. Com 6 seções e ciclo de 60s são ~8.640
# chamadas/dia — acima do limite de 5.000/dia do tier developer. Em produção,
# suba POLL_INTERVAL_SEC (ex.: 120 => ~4.320/dia) ou reduza GUARDIAN_SECTIONS.
GUARDIAN_PAGE_SIZE = int(os.environ.get("GUARDIAN_PAGE_SIZE", "15"))
GUARDIAN_TIMEOUT_SEC = float(os.environ.get("GUARDIAN_TIMEOUT_SEC", "15"))

# --- Agência Brasil (EBC): RSS oficial, já em português (pt-br) ---
# Feeds listados em https://agenciabrasil.ebc.com.br/feed/ . Padrão de URL:
# https://agenciabrasil.ebc.com.br/rss/<categoria>/feed.xml
# Categorias disponíveis: geral, politica, economia, internacional, justica,
# educacao, esportes, saude, direitos-humanos, alem do agregado
# "ultimasnoticias". Por padrão monitoramos as 4 prioritárias. Sobrescreva com
# AGENCIABRASIL_FEED_URLS (lista separada por vírgula) para mudar o conjunto.
# Mesmo idioma do BBC_FEED_URLS: vazio/ausente => usa os 4 feeds padrão.
# IMPORTANTE: este conteúdo JÁ vem em PT-BR — o classifier NÃO o manda para a
# DeepL (ver PT_NATIVE_SOURCES no serviço classifier). O rótulo de fonte é
# "Agência Brasil", no mesmo padrão de "BBC"/"Guardian".
DEFAULT_AGENCIABRASIL_FEEDS = [
    "https://agenciabrasil.ebc.com.br/rss/geral/feed.xml",
    "https://agenciabrasil.ebc.com.br/rss/politica/feed.xml",
    "https://agenciabrasil.ebc.com.br/rss/economia/feed.xml",
    "https://agenciabrasil.ebc.com.br/rss/internacional/feed.xml",
]
AGENCIABRASIL_FEED_URLS = [
    u.strip()
    for u in (
        os.environ.get("AGENCIABRASIL_FEED_URLS")
        or ",".join(DEFAULT_AGENCIABRASIL_FEEDS)
    ).split(",")
    if u.strip()
]

POLL_INTERVAL_SEC = int(os.environ.get("POLL_INTERVAL_SEC", "60"))

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _uid(seed):
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _clean_html_summary(raw, limit=600):
    """Os feeds da Agência Brasil trazem o corpo da matéria em HTML (logo em
    <img>, links, bloco 'Leia mais'). Tira as tags, resolve entidades, colapsa
    espaços e corta em `limit` chars — o resto do pipeline (classificação,
    prompt do comentário, verificador de fatos) espera resumo em texto puro,
    como já chega de BBC/Guardian."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip() + "…"
    return text


def fetch_bbc():
    """Lê todos os feeds RSS da BBC configurados. Uma falha em um feed é
    logada e não impede os demais."""
    items = []
    for url in BBC_FEED_URLS:
        try:
            feed = feedparser.parse(url)
            if getattr(feed, "bozo", 0) and not feed.entries:
                print(f"{TAG} → BBC: feed ilegível ({url}): {getattr(feed, 'bozo_exception', '?')}", flush=True)
                continue
            count = 0
            for entry in feed.entries:
                title = entry.get("title", "").strip()
                summary = entry.get("summary", "").strip()
                link = entry.get("link", "").strip()
                published = entry.get("published", "")
                if not title or not link:
                    continue
                items.append({
                    "id": _uid(link),
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "published": published,
                    "source": "BBC",
                })
                count += 1
            print(f"{TAG} → BBC: {count} itens de {url}", flush=True)
        except Exception as e:
            print(f"{TAG} → BBC: ERRO ao ler {url}: {type(e).__name__}: {e}", flush=True)
    return items


def fetch_guardian():
    """Consulta a Open Platform do The Guardian (uma chamada por seção).
    Sem GUARDIAN_API_KEY, a fonte é simplesmente ignorada (a BBC segue). Uma
    falha em uma seção é logada e não impede as demais."""
    if not GUARDIAN_API_KEY:
        return []

    items = []
    for section in GUARDIAN_SECTIONS:
        try:
            resp = requests.get(
                GUARDIAN_ENDPOINT,
                params={
                    "section": section,
                    "order-by": "newest",
                    "page-size": GUARDIAN_PAGE_SIZE,
                    "show-fields": "trailText,headline,firstPublicationDate",
                    "api-key": GUARDIAN_API_KEY,
                },
                timeout=GUARDIAN_TIMEOUT_SEC,
            )
            if resp.status_code != 200:
                print(
                    f"{TAG} → Guardian: HTTP {resp.status_code} na seção "
                    f"{section!r}: {resp.text[:200]}",
                    flush=True,
                )
                continue

            results = resp.json().get("response", {}).get("results", [])
            count = 0
            for art in results:
                # Só matérias — pula liveblog/gallery/interactive/etc.
                if art.get("type") and art.get("type") != "article":
                    continue
                fields = art.get("fields", {}) or {}
                title = (fields.get("headline") or art.get("webTitle") or "").strip()
                summary = (fields.get("trailText") or "").strip()
                link = (art.get("webUrl") or "").strip()
                published = (
                    art.get("webPublicationDate")
                    or fields.get("firstPublicationDate")
                    or ""
                )
                if not title or not link:
                    continue
                items.append({
                    "id": _uid(link),
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "published": published,
                    "source": "Guardian",
                })
                count += 1
            print(f"{TAG} → Guardian: {count} itens da seção {section!r}", flush=True)
        except Exception as e:
            print(
                f"{TAG} → Guardian: ERRO na seção {section!r}: "
                f"{type(e).__name__}: {e}",
                flush=True,
            )
    return items


def fetch_agenciabrasil():
    """Lê os feeds RSS da Agência Brasil (EBC). Mesmo padrão da BBC: uma falha
    em um feed é logada e não impede os demais. O conteúdo já vem em PT-BR e
    cada item sai rotulado com source="Agência Brasil"."""
    items = []
    for url in AGENCIABRASIL_FEED_URLS:
        try:
            feed = feedparser.parse(url)
            if getattr(feed, "bozo", 0) and not feed.entries:
                print(
                    f"{TAG} → Agência Brasil: feed ilegível ({url}): "
                    f"{getattr(feed, 'bozo_exception', '?')}",
                    flush=True,
                )
                continue
            count = 0
            for entry in feed.entries:
                title = entry.get("title", "").strip()
                summary = _clean_html_summary(entry.get("summary", ""))
                link = entry.get("link", "").strip()
                published = entry.get("published", "")
                if not title or not link:
                    continue
                items.append({
                    "id": _uid(link),
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "published": published,
                    "source": "Agência Brasil",
                })
                count += 1
            print(f"{TAG} → Agência Brasil: {count} itens de {url}", flush=True)
        except Exception as e:
            print(
                f"{TAG} → Agência Brasil: ERRO ao ler {url}: "
                f"{type(e).__name__}: {e}",
                flush=True,
            )
    return items


def collect_all():
    """Junta todas as fontes. Cada dict já vem com `source` preenchido."""
    return fetch_bbc() + fetch_guardian() + fetch_agenciabrasil()


def main():
    print(
        f"{TAG} → fontes: BBC ({len(BBC_FEED_URLS)} feeds)"
        + (
            f" + Guardian ({len(GUARDIAN_SECTIONS)} seções)"
            if GUARDIAN_API_KEY
            else " (Guardian desativado: sem GUARDIAN_API_KEY)"
        )
        + (
            f" + Agência Brasil ({len(AGENCIABRASIL_FEED_URLS)} feeds)"
            if AGENCIABRASIL_FEED_URLS
            else " (Agência Brasil desativada: AGENCIABRASIL_FEED_URLS vazia)"
        ),
        flush=True,
    )

    seen_ids = set()

    while True:
        try:
            news_items = collect_all()
            novos = 0
            for item in news_items:
                if item["id"] in seen_ids:
                    continue

                msg = {
                    "id": item["id"],
                    "title": item["title"],
                    "summary": item["summary"],
                    "link": item["link"],
                    "published": item["published"],
                    "source": item["source"],
                    "timestamp": time.time(),
                }

                r.xadd("news.raw", msg)
                print("Collector → news.raw:", json.dumps(msg, ensure_ascii=False))

                seen_ids.add(item["id"])
                novos += 1

            print(f"{TAG} → ciclo: {novos} novos, {len(seen_ids)} vistos no total", flush=True)
            time.sleep(POLL_INTERVAL_SEC)

        except Exception as e:
            print("Collector ERROR:", e)
            time.sleep(10)


if __name__ == "__main__":
    main()
