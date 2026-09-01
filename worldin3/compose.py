"""Composição vertical 9:16 do resumo, com ffmpeg.

Reaproveita os assets do estúdio (fundo, apresentadora, lower third, logo) e a
trilha licenciada em loop — os mesmos arquivos que o renderer/streamer usam
hoje. A apresentadora fica num plano de âncora (peito para cima), com a base da
imagem dela encaixada atrás da faixa de manchete — que funciona como legenda
para quem assiste sem som (uma manchete por fatia de tempo) e como lower third.

Lip-sync D-ID é opcional e cai no avatar estático em qualquer falha — o mesmo
`renderer/lipsync.py` (copiado para `worldin3/lipsync.py` no build).
"""

import os
import shutil
import subprocess
import tempfile

from . import config

_CHROMA = ("0x00B140", "0.14", "0.06")  # mesmos valores do renderer


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        raise RuntimeError(f"ffprobe não leu duração de {path}: {out.stderr[:200]}")


def _render_music_bed(music_path, seconds, out_path):
    """Pré-renderiza a trilha em loop já no tamanho EXATO (`seconds`), num
    ffmpeg à parte e trivial: um único stream de áudio, sem `amix`, sem vídeo.
    Esse comando SEMPRE finaliza limpo — não há sincronização entre streams
    para travar. O comando principal então recebe a cama com `-i` simples, sem
    `-stream_loop`: sem fronteira de loop dentro do filtergraph, que era o que
    provocava o deadlock de finalização (`-stream_loop` + `amix duration=first`
    + `-t` → todas as threads em futex_wait na volta do loop perto do fim,
    moov atom nunca fechava)."""
    res = subprocess.run(
        ["ffmpeg", "-y", "-stream_loop", "-1", "-i", music_path,
         "-t", f"{seconds:.3f}", "-ac", "2", "-ar", "48000",
         "-c:a", "aac", "-b:a", "192k", out_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if res.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError(f"não renderizou a cama musical: {res.stderr[-800:]}")
    return out_path


def _wrap(text, width=24, max_lines=2):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 <= width or not cur:
            cur = (cur + " " + w).strip()
        else:
            lines.append(cur)
            cur = w
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and (cur or words):
        lines[-1] = lines[-1].rstrip(" .,;:") + "…"
    return "\n".join(lines[:max_lines])


def _maybe_lipsync(news_id, voice_path):
    if not config.ENABLE_LIPSYNC:
        return None
    try:
        from .lipsync import get_lipsync_video  # cópia de renderer/lipsync.py

        return get_lipsync_video(news_id, voice_path)
    except Exception as e:  # nunca quebra a composição
        print(f"[worldin3/compose] lip-sync indisponível ({e}); avatar estático.", flush=True)
        return None


def compose_vertical(voice_path, captions, out_path, render_seconds, news_id,
                     ticker_text=None):
    """Monta o mp4 vertical. `captions` = lista de legendas PT (uma por
    manchete, mesma ordem). `render_seconds` = duração final desejada (já
    limitada a config.MAX_SECONDS pelo chamador). `ticker_text` = texto único
    do ticker rolante (se None, usa as próprias legendas em rotação).
    Retorna (out_path, lipsync_bool)."""
    W, H = config.OUT_W, config.OUT_H
    lipsync = _maybe_lipsync(news_id, voice_path)

    tmpdir = tempfile.mkdtemp(prefix="wi3_")
    try:
        # --- textfiles das legendas (uma por manchete, fatia de tempo igual) ---
        n = len(captions)
        slot = render_seconds / n
        cap_files = []
        for i, cap in enumerate(captions):
            p = os.path.join(tmpdir, f"cap_{i}.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write(_wrap(cap))
            cap_files.append(p)
        period_file = os.path.join(tmpdir, "period.txt")
        with open(period_file, "w", encoding="utf-8") as f:
            f.write(config.period_label().upper())

        # --- texto do ticker rolante (uma linha só, manchetes em rotação) ---
        tk_text = (ticker_text or "  •  ".join(c.strip() for c in captions)).strip()
        ticker_file = os.path.join(tmpdir, "ticker.txt")
        with open(ticker_file, "w", encoding="utf-8") as f:
            f.write(tk_text)

        # --- entradas ---
        if lipsync:
            avatar_in = ["-stream_loop", "-1", "-i", lipsync]
            av_filter = (
                f"[1:v]scale={config.AVATAR_W}:{config.AVATAR_H},"
                f"chromakey={_CHROMA[0]}:{_CHROMA[1]}:{_CHROMA[2]},"
                f"despill=type=green:mix=0.5:expand=0[av];"
            )
        else:
            avatar_in = ["-loop", "1", "-i", config.AVATAR_IMG]
            av_filter = f"[1:v]scale={config.AVATAR_W}:{config.AVATAR_H}[av];"

        fade = max(0.0, render_seconds - 0.5)
        afade = max(0.0, render_seconds - 0.6)

        cap_cy = config.CAPTION_Y + config.CAPTION_STRIP_H / 2  # centro da faixa
        parts = [
            f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H}:(iw-ow)/2+{config.BG_CROP_X}:(ih-oh)/2+{config.BG_CROP_Y},"
            f"setsar=1[bg];",
            av_filter,
            # Âncora encaixada na faixa de legenda: a linha de corte (topo da
            # faixa) cai nos ANTEBRAÇOS dela — as mãos entrelaçadas ficam
            # inteiras ESCONDIDAS atrás da faixa (ver nota do AVATAR_CAPTION_TUCK
            # em config.py). overlay_y = CAPTION_Y + TUCK - AVATAR_H.
            f"[bg][av]overlay=(W-w)/2:"
            f"{config.CAPTION_Y}+{config.AVATAR_CAPTION_TUCK}-h[b1];",
            "[2:v]scale=220:83[lg];[b1][lg]overlay=40:70[b2];",
            # Faixa de legenda: retângulo semiopaco (garante leitura sobre
            # qualquer fundo E tampa a apresentadora) + a arte lowerthird.png
            # por cima, como moldura.
            f"[b2]drawbox=x=0:y={config.CAPTION_Y}:w={W}:h={config.CAPTION_STRIP_H}:"
            f"color=black@{config.CAPTION_BOX_OPACITY}:t=fill[b2b];",
            f"[3:v]scale={W}:{config.CAPTION_STRIP_H}[lt];"
            f"[b2b][lt]overlay=0:{config.CAPTION_Y}[b3];",
            f"[b3]drawtext=fontfile={config.FONT_BOLD}:text='O MUNDO EM 3 MINUTOS':"
            f"fontcolor=white:fontsize=46:x=(w-tw)/2:y=140:"
            f"box=1:boxcolor=black@0.38:boxborderw=18[t1];",
            f"[t1]drawtext=fontfile={config.FONT_REGULAR}:textfile='{period_file}':"
            f"fontcolor=0x00E0FF:fontsize=30:x=(w-tw)/2:y=202[t2];",
        ]
        last = "t2"
        for i, cf in enumerate(cap_files):
            nxt = f"c{i}"
            start = i * slot
            end = render_seconds if i == n - 1 else (i + 1) * slot
            parts.append(
                f"[{last}]drawtext=fontfile={config.FONT_BOLD}:textfile='{cf}':"
                f"expansion=none:fontcolor=white:fontsize={config.CAPTION_FONTSIZE}:"
                f"line_spacing=10:x=(w-tw)/2:y={cap_cy}-(text_h/2):"
                f"box=0:enable='between(t,{start:.3f},{end:.3f})'[{nxt}];"
            )
            last = nxt

        # --- ticker rolante + escurecimento da base (preenche a área que
        #     sobrava vazia entre a faixa de legenda e o rodapé) ---
        if config.ENABLE_TICKER:
            tk_top = config.CAPTION_Y + config.CAPTION_STRIP_H
            tk_bot = tk_top + config.TICKER_H
            tk_ty = tk_top + (config.TICKER_H - config.TICKER_FONTSIZE) // 2
            scrim_h = max(0, H - tk_bot)
            parts.append(
                f"[{last}]drawbox=x=0:y={tk_top}:w={W}:h={config.TICKER_H}:"
                f"color=black@{config.CAPTION_BOX_OPACITY}:t=fill[tk0];"
                f"[tk0]drawbox=x=0:y={tk_top}:w={W}:h=3:color=0x00E0FF:t=fill[tk1];"
                f"[tk1]drawbox=x=0:y={tk_bot - 3}:w={W}:h=3:color=0x00E0FF:t=fill[tk2];"
            )
            _src = "tk2"
            if scrim_h > 0 and config.BOTTOM_SCRIM_OPACITY > 0:
                # degradê: mais claro no topo, aprofundando na base
                steps = 8
                seg = scrim_h / steps
                base = config.BOTTOM_SCRIM_OPACITY
                for k in range(steps):
                    op = min(0.95, base + 0.24 * (k / (steps - 1)))
                    y0 = int(tk_bot + k * seg)
                    h_k = int(H - y0) if k == steps - 1 else int(seg) + 1
                    parts.append(
                        f"[{_src}]drawbox=x=0:y={y0}:w={W}:h={h_k}:"
                        f"color=black@{op:.3f}:t=fill[sc{k}];"
                    )
                    _src = f"sc{k}"
            parts.append(
                f"[{_src}]drawtext=fontfile={config.FONT_BOLD}:textfile='{ticker_file}':"
                f"expansion=none:fontcolor=white:fontsize={config.TICKER_FONTSIZE}:"
                f"x=w-mod(t*{config.TICKER_SPEED}\\,w+tw):y={tk_ty}[tkout];"
            )
            last = "tkout"

        parts.append(
            f"[{last}]fade=t=in:st=0:d=0.3,fade=t=out:st={fade:.3f}:d=0.5[vout];"
        )
        parts.append(
            f"[4:a]volume={config.VOICE_GAIN}[vc];"
            f"[5:a]volume={config.MUSIC_GAIN}[mx];"
            f"[vc][mx]amix=inputs=2:duration=shortest:dropout_transition=3:normalize=0,"
            f"afade=t=out:st={afade:.3f}:d=0.6,"
            f"loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
        )
        filter_complex = "".join(parts)

        # Trilha pré-renderizada em loop no tamanho exato (ver _render_music_bed):
        # entra como `-i` simples, SEM `-stream_loop`. A cama tem exatamente
        # render_seconds e o `amix` fecha em `duration=shortest`, então não há
        # volta de loop perto do fim para travar o ffmpeg na finalização.
        bed_path = os.path.join(tmpdir, "music_bed.m4a")
        _render_music_bed(config.MUSIC_IMG, render_seconds, bed_path)

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", config.BACKGROUND_IMG,
            *avatar_in,
            "-loop", "1", "-i", config.LOGO_IMG,
            "-loop", "1", "-i", config.LOWERTHIRD_IMG,
            "-i", voice_path,
            "-i", bed_path,
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "[aout]",
            "-t", f"{render_seconds:.3f}",
            "-shortest",
            "-r", "30",
            "-threads", "4",
            "-filter_complex_threads", "4",
            "-max_muxing_queue_size", "1024",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-level", "4.1",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            out_path,
        ]
        print("[worldin3/compose] renderizando composição vertical 9:16…", flush=True)
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            print(res.stderr[-4000:], flush=True)
            raise RuntimeError(f"ffmpeg falhou (rc={res.returncode})")
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError("mp4 final não foi gerado")
        return out_path, bool(lipsync)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def extract_frame(video_path, out_png, at_seconds):
    res = subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{at_seconds:.3f}", "-i", video_path,
         "-frames:v", "1", "-q:v", "2", out_png],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if res.returncode != 0 or not os.path.exists(out_png):
        raise RuntimeError(f"não extraiu frame: {res.stderr[-800:]}")
    return out_png
