"""Seleção das manchetes reais mais recentes de `news.ready`.

Mesmo critério do ticker do renderer (`build_ticker_text`): varre o stream de
trás para frente, ignora blocos promocionais e repetições. Aqui, além disso:
  - descarta rótulos de programa/feed que às vezes chegam como "título"
    (config.TITLE_BLOCKLIST) e títulos curtos demais para ser manchete;
  - carrega também o `commentary` (análise já gerada E já aprovada pelos 3
    revisores para aquela notícia individual) — ele vira o material-fonte que o
    verificador de fatos usa para checar o roteiro do resumo.
"""

import redis

from . import config

r = redis.Redis(host="redis", port=6379, decode_responses=True, socket_timeout=10)

_PROMO_CATS = ("promoção", "promocao", "promo")


def _norm(s):
    return " ".join((s or "").split())


def _scan(stream, n):
    entries = r.xrevrange(stream, "+", "-", count=config.HEADLINE_SCAN)
    out = []
    seen = set()
    for _id, d in entries:
        cat = (d.get("category") or "").strip().lower()
        if cat in _PROMO_CATS:
            continue
        title = _norm(d.get("title"))
        key = title.lower()
        if not title or key in seen:
            continue
        if key in config.TITLE_BLOCKLIST:
            continue
        if len(title.split()) < config.HEADLINE_MIN_WORDS:
            continue
        seen.add(key)
        out.append(
            {
                "id": d.get("id", ""),
                "title": title,
                "title_original": _norm(d.get("title_original")) or title,
                "category": _norm(d.get("category")) or "Geral",
                "commentary": _norm(d.get("commentary")),
                "source": _norm(d.get("source")),
            }
        )
        if len(out) >= n:
            break
    return out, len(entries)


def fetch_headlines(n=None):
    """Retorna uma lista de até `n` dicts, do mais recente para o mais antigo:
        {id, title, title_original, category, commentary, source}
    Varre `news.ready`; se vier vazio, tenta `NEWS_FALLBACK_STREAM` (news.final).
    Levanta RuntimeError se não achar nenhuma manchete real utilizável."""
    n = n or config.HEADLINE_COUNT
    out, scanned = _scan(config.NEWS_READY_STREAM, n)
    if not out and config.NEWS_FALLBACK_STREAM:
        fb = config.NEWS_FALLBACK_STREAM
        print(f"[worldin3/headlines] {config.NEWS_READY_STREAM} vazio "
              f"(varri {scanned}); usando fallback {fb}.", flush=True)
        out, scanned = _scan(fb, n)

    if not out:
        raise RuntimeError(
            f"nenhuma manchete real utilizável em {config.NEWS_READY_STREAM} "
            f"nem no fallback {config.NEWS_FALLBACK_STREAM} (varri {scanned} entradas)"
        )
    return out


def sources_label(headlines):
    labels = sorted({h["source"] for h in headlines if h["source"]})
    return ", ".join(labels) if labels else "BBC"
