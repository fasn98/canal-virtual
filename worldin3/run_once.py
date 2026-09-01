"""PARTE 2 — roda UMA edição do "O Mundo em 3 Minutos" de ponta a ponta.

Subcomandos:
  generate       manchetes -> roteiro (Claude + 3 revisores) -> TTS real
                 (ElevenLabs) -> composição vertical. Grava mp4/frame/roteiro/
                 metadata e PARA. Não sobe nada. (usar no teste manual)
  upload-last    sobe o mp4 da última edição gerada (worldin3_edition_last.json)
                 no YouTube como config.PRIVACY_STATUS e adiciona à playlist.
  full           generate + upload numa tacada (o agendador usa este).
  tubeoptimizer  dispara o TubeOptimizer para a URL da última edição publicada
                 (autoPublish=true). Passo manual, pós-aprovação.

Uso (dentro do container worldin3):
  python -m worldin3.run_once generate [--ignore-budget]
  python -m worldin3.run_once upload-last
  python -m worldin3.run_once full
  python -m worldin3.run_once tubeoptimizer
"""

import argparse
import datetime
import json
import os
import sys

from . import compose, config, headlines as hl, publish, reviewbridge
from .review import ReviewUnavailable


def _sep(t):
    print("\n" + "=" * 72 + f"\n {t}\n" + "=" * 72, flush=True)


def _now():
    return datetime.datetime.now(config.tz())


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_last():
    p = config.edition_paths("x")["last"]
    if not os.path.exists(p):
        sys.exit(f"nenhuma edição gerada ainda (falta {p}). Rode 'generate' antes.")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
def do_generate(ignore_budget=False):
    now = _now()
    stamp = now.strftime("%Y%m%d-%H%M")
    paths = config.edition_paths(stamp)
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    _sep(f"1) MANCHETES REAIS de {config.NEWS_READY_STREAM}")
    items = hl.fetch_headlines(config.HEADLINE_COUNT)
    for i, h in enumerate(items, 1):
        print(f"  {i}. [{h['category']}/{h['source'] or '?'}] {h['title']}", flush=True)

    _sep("2) ROTEIRO + 3) REVISÃO PELOS 3 AGENTES")
    if not config.ENABLE_EDITORIAL_REVIEW:
        print("  ⚠️  ENABLE_EDITORIAL_REVIEW=false — revisão DESLIGADA.", flush=True)
    client = config.anthropic_client()

    target = config.TARGET_WORDS
    script = caps = None
    trilha = []
    for shorten in range(config.SHORTEN_ATTEMPTS + 1):
        try:
            script, caps, trilha = reviewbridge.produce_approved_digest(client, items, target)
        except ReviewUnavailable as e:
            sys.exit(f"✖ Revisão indisponível (API): {e}. Nada gerado.")
        if script is None:
            last = trilha[-1]["motivo"] if trilha else "(sem motivo)"
            sys.exit(f"✖ ROTEIRO REPROVADO pela revisão após "
                     f"{config.MAX_CORRECTION_ATTEMPTS} correções. Último motivo: {last}")
        wc = len(script.split())
        # ~1.95 palavras/s: ritmo MEDIDO da voz PT-BR da ElevenLabs (um roteiro
        # de 375 palavras rendeu 199s de áudio). O 2.6 anterior era otimista
        # demais e o loop de encurtamento nunca disparava — o áudio estourava
        # MAX_SECONDS e o vídeo era cortado no fim (perdendo o CTA).
        est = wc / 1.95
        if est <= config.MAX_SECONDS or shorten == config.SHORTEN_ATTEMPTS:
            break
        # 0.85 (não 0.92): o modelo costuma entregar acima do alvo pedido, então
        # sobra-corrige para o próximo passo já cair dentro de MAX_SECONDS.
        target = int(target * (config.MAX_SECONDS / est) * 0.85)
        print(f"  roteiro ~{est:.0f}s pode passar de {config.MAX_SECONDS:.0f}s; "
              f"regenerando com alvo {target} palavras…", flush=True)

    print(f"\n  ✔ Roteiro LIBERADO (tentativa {trilha[-1]['attempt']}; "
          f"{len(script.split())} palavras).", flush=True)
    for i, c in enumerate(caps, 1):
        print(f"     legenda {i}: {c}", flush=True)
    with open(paths["script"], "w", encoding="utf-8") as f:
        f.write(script + "\n\n===LEGENDAS DO RODAPÉ (PT)===\n")
        for i, c in enumerate(caps, 1):
            f.write(f"{i}. {c}\n")

    _sep("4) SÍNTESE DE VOZ (ElevenLabs — voz/modelo do synthesizer)")
    from . import tts
    voice_path = os.path.join(config.ASSETS_DIR, "worldin3", "audio", f"{stamp}.mp3")
    tts.synthesize(script, voice_path, ignore_budget=ignore_budget)
    voice_dur = compose.probe_duration(voice_path)
    render_seconds = min(voice_dur, config.MAX_SECONDS)
    trimmed = voice_dur > config.MAX_SECONDS + 0.05
    print(f"  áudio {voice_dur:.2f}s -> vídeo cortado em {render_seconds:.2f}s "
          f"(teto {config.MAX_SECONDS:.0f}s){'  ⚠️ cortado' if trimmed else ''}", flush=True)

    _sep("5) COMPOSIÇÃO VERTICAL 9:16")
    ticker_text = "  •  ".join(c.strip() for c in caps)
    compose.compose_vertical(voice_path, caps, paths["mp4"], render_seconds,
                             f"worldin3-{stamp}", ticker_text=ticker_text)
    final_dur = compose.probe_duration(paths["mp4"])
    compose.extract_frame(paths["mp4"], paths["frame"], at_seconds=min(6.0, final_dur / 2))

    meta = {
        "stamp": stamp,
        "generated_at": now.isoformat(),
        "title": publish.build_title(now),
        "description": publish.build_description(items, now),
        "headlines": [h["title"] for h in items],
        "captions": caps,
        "duration_sec": round(final_dur, 2),
        "trimmed": trimmed,
        "mp4": paths["mp4"],
        "frame": paths["frame"],
        "script": paths["script"],
        "review_attempts": trilha,
        "privacy_intended": config.PRIVACY_STATUS,
        "uploaded": None,
    }
    _write_json(paths["metadata"], meta)
    _write_json(paths["last"], meta)

    _sep("EDIÇÃO GERADA — AGUARDANDO APROVAÇÃO (nada foi ao ar)")
    print(f"  título ...... {meta['title']}")
    print(f"  duração ..... {final_dur:.2f}s  ({int(final_dur//60)}:{final_dur%60:05.2f})")
    print(f"  vídeo ....... {paths['mp4']}  ({os.path.getsize(paths['mp4'])/1e6:.1f} MB)")
    print(f"  frame ....... {paths['frame']}")
    print(f"  roteiro ..... {paths['script']}")
    print(f"  metadata .... {paths['metadata']}")
    print(f"\n  privacyStatus previsto no upload: {config.PRIVACY_STATUS} "
          f"(WORLDIN3_PRIVACY_STATUS)")
    print("  Para subir depois de aprovar:  python -m worldin3.run_once upload-last", flush=True)
    return meta


# --------------------------------------------------------------------------
def do_upload_last():
    meta = _load_last()
    if meta.get("uploaded"):
        print(f"⚠️  esta edição já foi enviada: {meta['uploaded'].get('url')}", flush=True)
        return meta
    mp4 = meta["mp4"]
    if not os.path.exists(mp4):
        sys.exit(f"mp4 não encontrado: {mp4}")
    now = datetime.datetime.fromisoformat(meta["generated_at"])
    # Reconstrói a lista de headlines no formato que o build_description espera.
    fake_headlines = [{"title": t} for t in meta["headlines"]]

    _sep("UPLOAD NO YOUTUBE")
    result = publish.publish_edition(mp4, fake_headlines, now=now)

    meta["uploaded"] = {
        "at": _now().isoformat(),
        **result,
    }
    paths = config.edition_paths(meta["stamp"])
    _write_json(paths["metadata"], meta)
    _write_json(paths["last"], meta)

    _sep("UPLOAD CONCLUÍDO")
    print(f"  {result['title']}")
    print(f"  {result['url']}   ({result['privacy']})")
    if result.get("playlist_item_id"):
        print(f"  playlist: {result['playlist_id']} (item {result['playlist_item_id']})")
    print("\n  TubeOptimizer NÃO foi chamado. Depois de conferir o vídeo no ar:")
    print("     python -m worldin3.run_once tubeoptimizer", flush=True)
    return meta


def do_tubeoptimizer():
    meta = _load_last()
    up = meta.get("uploaded")
    if not up or not up.get("url"):
        sys.exit("a última edição ainda não foi enviada ao YouTube.")
    _sep("TUBEOPTIMIZER (autoPublish=true)")
    data = publish.push_to_tubeoptimizer(up["url"], auto_publish=True)
    meta["tubeoptimizer"] = {"at": _now().isoformat(), "response": data}
    paths = config.edition_paths(meta["stamp"])
    _write_json(paths["metadata"], meta)
    _write_json(paths["last"], meta)
    print(f"  OK: {json.dumps(data, ensure_ascii=False)[:500]}", flush=True)
    return meta


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["generate", "upload-last", "full", "tubeoptimizer"])
    ap.add_argument("--ignore-budget", action="store_true",
                    help="ignora o freio diário da ElevenLabs (só no generate/full)")
    args = ap.parse_args(argv)

    if args.cmd == "generate":
        do_generate(ignore_budget=args.ignore_budget)
    elif args.cmd == "upload-last":
        do_upload_last()
    elif args.cmd == "full":
        do_generate(ignore_budget=args.ignore_budget)
        do_upload_last()
    elif args.cmd == "tubeoptimizer":
        do_tubeoptimizer()
    return 0


if __name__ == "__main__":
    sys.exit(main())
