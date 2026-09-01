"""Agendamento das 3 edições diárias do "O Mundo em 3 Minutos".

Helpers de horário + o LOOP da Parte 2 (`run_forever`), que acorda nos
horários de `WORLDIN3_SCHEDULE_TIMES` (padrão 08:00,14:00,20:00) no fuso
`WORLDIN3_SCHEDULE_TZ` (padrão America/Sao_Paulo) e roda o pipeline completo
+ upload (`run_once.full`).

Anti-duplicata: cada slot marca `worldin3:ran:<data>:<HH:MM>` no Redis (TTL
2 dias). Assim, restart do container no meio do dia NÃO re-dispara um slot já
feito, e o "catch-up" (subir alguns minutos depois do horário) roda no máximo
uma vez.

Uso:
  python -m worldin3.scheduler            # imprime a agenda e sai
  python -m worldin3.scheduler --loop     # fica rodando (o serviço no compose)
  python -m worldin3.scheduler --run-now  # dispara UMA edição agora e sai
"""

import argparse
import datetime
import sys
import time

from . import config

TAG = "worldin3/scheduler"
_RAN_TTL_SEC = 2 * 24 * 3600


def _log(msg):
    print(f"[{TAG}] {datetime.datetime.now(config.tz()):%Y-%m-%d %H:%M:%S %Z} | {msg}",
          flush=True)


def parse_times(items=None):
    items = items or config.SCHEDULE_TIMES
    out = []
    for it in items:
        hh, mm = it.split(":")
        out.append((int(hh), int(mm)))
    return sorted(out)


def next_runs(now=None, count=3):
    """Próximos `count` disparos, como datetimes AWARE no fuso configurado."""
    tz = config.tz()
    now = now or datetime.datetime.now(tz)
    slots = parse_times()
    runs = []
    day = now.date()
    while len(runs) < count:
        for hh, mm in slots:
            cand = datetime.datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
            if cand > now:
                runs.append(cand)
        day = day + datetime.timedelta(days=1)
    return runs[:count]


def _slots_today(now):
    tz = config.tz()
    return [
        datetime.datetime(now.year, now.month, now.day, hh, mm, tzinfo=tz)
        for hh, mm in parse_times()
    ]


def describe():
    tz = config.tz()
    now = datetime.datetime.now(tz)
    server = datetime.datetime.now().astimezone()
    lines = [
        f"Fuso de agendamento : {config.SCHEDULE_TZ}  (agora: {now:%Y-%m-%d %H:%M %Z})",
        f"Fuso do servidor    : {server.tzname()}     (agora: {server:%Y-%m-%d %H:%M %Z})",
        f"Horários (config)   : {', '.join(config.SCHEDULE_TIMES)}",
        f"Catch-up            : {config.SCHEDULE_CATCHUP_MIN} min",
        f"privacyStatus       : {config.PRIVACY_STATUS}",
        f"Playlist            : {config.PLAYLIST_ID or '(nenhuma)'}",
        "Próximos disparos:",
    ]
    for run in next_runs(now=now, count=6):
        lines.append(
            f"  - {run:%a %Y-%m-%d %H:%M %Z}  "
            f"(= {run.astimezone(datetime.timezone.utc):%H:%M} UTC)"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
def _redis():
    import redis
    return redis.Redis(host="redis", port=6379, decode_responses=True, socket_timeout=10)


def _slot_key(dt):
    return f"worldin3:ran:{dt:%Y-%m-%d}:{dt:%H:%M}"


def _already_ran(r, dt):
    try:
        return bool(r.get(_slot_key(dt)))
    except Exception:
        return False


def _mark_ran(r, dt, info=""):
    try:
        r.set(_slot_key(dt), info or "1", ex=_RAN_TTL_SEC)
    except Exception as e:
        _log(f"AVISO: não consegui marcar o slot no Redis ({e}).")


def _fire(slot_dt, r):
    from . import run_once
    _log(f"disparando edição do slot {slot_dt:%H:%M} …")
    try:
        run_once.do_generate(ignore_budget=False)
        run_once.do_upload_last()
        _mark_ran(r, slot_dt, info=datetime.datetime.now(config.tz()).isoformat())
        _log(f"slot {slot_dt:%H:%M} concluído.")
    except SystemExit as e:
        _log(f"slot {slot_dt:%H:%M} ABORTADO: {e}")
    except Exception as e:
        _log(f"slot {slot_dt:%H:%M} ERRO ({type(e).__name__}): {e}")


def run_forever():
    tz = config.tz()
    r = _redis()
    _log("loop iniciado.\n" + describe())

    # Catch-up na subida: algum slot de hoje já passou há <= CATCHUP e não rodou?
    now = datetime.datetime.now(tz)
    for slot in _slots_today(now):
        age_min = (now - slot).total_seconds() / 60.0
        if 0 <= age_min <= config.SCHEDULE_CATCHUP_MIN and not _already_ran(r, slot):
            _log(f"catch-up: slot {slot:%H:%M} passou há {age_min:.0f} min; rodando agora.")
            _fire(slot, r)

    while True:
        now = datetime.datetime.now(tz)
        nxt = next_runs(now=now, count=1)[0]
        wait = (nxt - now).total_seconds()
        _log(f"próximo disparo: {nxt:%Y-%m-%d %H:%M %Z} (em {wait/3600:.2f} h).")
        # Dorme em fatias de <=15 min para tolerar mudança de horário / DST.
        while True:
            now = datetime.datetime.now(tz)
            wait = (nxt - now).total_seconds()
            if wait <= 0:
                break
            time.sleep(min(wait, 900))
        if not _already_ran(r, nxt):
            _fire(nxt, r)
        else:
            _log(f"slot {nxt:%H:%M} já constava como feito; pulando.")
        time.sleep(60)  # garante que passamos do minuto do slot


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true", help="fica rodando e dispara nos horários")
    ap.add_argument("--run-now", action="store_true", help="dispara UMA edição agora e sai")
    args = ap.parse_args(argv)

    if args.run_now:
        from . import run_once
        run_once.do_generate(ignore_budget=False)
        run_once.do_upload_last()
        return 0
    if args.loop:
        run_forever()
        return 0
    print(describe())
    return 0


if __name__ == "__main__":
    sys.exit(main())
