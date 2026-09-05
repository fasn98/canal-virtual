"""
Teste manual ISOLADO do provedor AVATAR_PROVIDER=musetalk — UMA geração, FORA do
loop 24/7. Não toca no consumer group, no final.mp4 de produção nem em
news.block. Serve para você validar o resultado antes de qualquer automação.

Uso (na RAIZ do repo, com o pod GPU já de pé):
  python3 -m renderer.test_musetalk --text "Boa tarde. ..." \
      --title "Manchete da notícia" --category Economia \
      --out /tmp/musetalk_bloco.mp4

  # a partir de um id já processado (lê o commentary do stream; precisa de Redis):
  REDIS_HOST=localhost python3 -m renderer.test_musetalk --item 1a2b3c4d5e6f7a8b

  # ligando/desligando o pod GPU no ciclo (start_gpu/stop_gpu):
  python3 -m renderer.test_musetalk --text "..." --lifecycle

  # chroma-key real (em vez da "caixa nativa"), calibrando contra o idle novo:
  MUSETALK_CHROMA_COLOR=0x00B140 MUSETALK_CHROMA_SIMILARITY=0.14 \
  MUSETALK_CHROMA_BLEND=0.06 python3 -m renderer.test_musetalk --text "..." --chroma

O script carrega o .env da raiz (sem sobrescrever env já setado). Requer:
  GPU_SERVER_URL, INFERENCE_SERVER_API_KEY   (GPU_LIPSYNC_REAL é forçado p/ true)
Para --lifecycle: RUNPOD_API_KEY + RUNPOD_POD_ID (controle direto na API do RunPod).
--chroma liga MUSETALK_CHROMA_ENABLED=true só nesta execução (não mexe no
.env nem no docker-compose — a produção continua em "caixa nativa" até você
decidir ligar lá também).
"""

import argparse
import os
import re
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_HEX16 = re.compile(r"^[0-9a-f]{16}$")


def _load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k and k not in os.environ:
                os.environ[k] = v


def _resolve_item(value, default_title, default_category):
    """value = texto literal OU id hex de 16 chars. Se for id e REDIS_HOST
    estiver setado, busca commentary/title/category em news.final/news.ready."""
    v = value.strip()
    if not _HEX16.match(v.lower()):
        return {"id": "test-musetalk", "commentary": value,
                "title": default_title, "category": default_category}
    host = os.environ.get("REDIS_HOST", "").strip()
    if not host:
        print("AVISO: --item parece um id mas REDIS_HOST não está setado; "
              "tratando o valor como texto literal.", file=sys.stderr)
        return {"id": v, "commentary": value,
                "title": default_title, "category": default_category}
    import redis
    rc = redis.Redis(host=host, port=int(os.environ.get("REDIS_PORT", "6379")),
                     decode_responses=True, socket_timeout=5)
    for stream in ("news.final", "news.ready"):
        try:
            entries = rc.xrevrange(stream, "+", "-", count=500)
        except Exception as e:
            print(f"AVISO: falha ao ler {stream}: {e}", file=sys.stderr)
            continue
        for _eid, d in entries:
            if (d.get("id") or "").strip() == v:
                return {"id": d.get("id"), "commentary": d.get("commentary", ""),
                        "title": d.get("title") or default_title,
                        "category": d.get("category") or default_category}
    raise SystemExit(f"id {v!r} não encontrado em news.final/news.ready")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--item", help="id hex de 16 chars (lê do Redis) OU texto de exemplo literal")
    g.add_argument("--text", help="texto do apresentador (literal)")
    ap.add_argument("--title", default="TESTE MuseTalk", help="manchete do lower third")
    ap.add_argument("--category", default="Geral")
    ap.add_argument("--out", default=os.path.join(_REPO_ROOT, "musetalk_test_output.mp4"))
    ap.add_argument("--assets-dir", default=os.path.join(_REPO_ROOT, "volumes", "assets"))
    ap.add_argument("--ticker-dir", default=os.path.join(_REPO_ROOT, "volumes", "ticker"))
    ap.add_argument("--keep-presenter", action="store_true",
                    help="mantém também o MP4 cru do MuseTalk (antes dos gráficos)")
    ap.add_argument("--lifecycle", action="store_true",
                    help="liga o pod (start_gpu) antes e desliga (stop_gpu) depois")
    ap.add_argument("--chroma", action="store_true",
                    help="liga MUSETALK_CHROMA_ENABLED=true só nesta execução "
                    "(chroma-key real em vez da 'caixa nativa'; calibre "
                    "MUSETALK_CHROMA_COLOR/SIMILARITY/BLEND no .env antes)")
    args = ap.parse_args(argv)

    _load_dotenv(os.path.join(_REPO_ROOT, ".env"))
    os.environ["AVATAR_PROVIDER"] = "musetalk"
    os.environ["GPU_LIPSYNC_REAL"] = "true"
    if args.chroma:
        os.environ["MUSETALK_CHROMA_ENABLED"] = "true"
    for req in ("GPU_SERVER_URL", "INFERENCE_SERVER_API_KEY"):
        if not os.environ.get(req, "").strip():
            raise SystemExit(f"{req} não configurado (env ou .env na raiz).")

    src = args.item if args.item is not None else args.text
    it = _resolve_item(src, args.title, args.category)
    text, title, category, nid = (
        it["commentary"], it["title"], it["category"], it["id"],
    )
    if not text or not text.strip():
        raise SystemExit("texto vazio — nada a gerar.")

    from renderer.musetalk import compose_presenter_block, get_presenter_video
    from lib import gpu_client

    print(f"[test_musetalk] provider=musetalk id={nid} chars={len(text)}")
    print(f"[test_musetalk] title={title!r} category={category!r} out={args.out}")

    out_dir = os.path.dirname(os.path.abspath(args.out)) or "."
    os.makedirs(out_dir, exist_ok=True)

    def _run():
        presenter = get_presenter_video(nid, text, out_dir=out_dir)
        compose_presenter_block(
            nid, text, title, category,
            assets_dir=args.assets_dir, ticker_dir=args.ticker_dir,
            out_path=args.out, presenter_mp4=presenter, ticker_text=title,
        )
        if args.keep_presenter:
            print(f"[test_musetalk] MP4 cru do MuseTalk mantido em {presenter}")
        else:
            try:
                os.remove(presenter)
            except OSError:
                pass

    if args.lifecycle:
        with gpu_client.gpu_session():
            _run()
    else:
        _run()

    sz = os.path.getsize(args.out) if os.path.exists(args.out) else 0
    print(f"[test_musetalk] OK → {args.out} ({sz} bytes)")
    print("[test_musetalk] Inspecione o arquivo. NADA foi publicado no pipeline.")


if __name__ == "__main__":
    main()
