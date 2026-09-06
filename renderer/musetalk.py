"""
Provedor ALTERNATIVO do vídeo do apresentador via MuseTalk/Chatterbox no pod GPU.

Ativado por AVATAR_PROVIDER=musetalk (o renderer usa D-ID/estático por padrão e
NEM importa este módulo nesse caso). Isolado igual a renderer/lipsync.py: o
renderer só chama compose_presenter_block().

FLUXO (AVATAR_PROVIDER=musetalk), por notícia:
  1. get_presenter_video() -> lib/gpu_client.generate_presenter_chunked():
       a. texto dividido em blocos de frases <= MUSETALK_TTS_MAX_CHARS
          (o Chatterbox trunca texto longo; o proxy do RunPod corta /lipsync
          em ~100 s);
       b. POST {GPU_SERVER_URL}/tts por bloco -> WAV 24kHz mono concatenado;
       c. gate de QA sobre o WAV concatenado (_validate_presenter_media);
       d. POST {GPU_SERVER_URL}/lipsync por bloco -> MP4 por bloco;
       e. concatena os MP4s -> vídeo do apresentador com A VOZ EMBUTIDA;
       f. QA final sobre o MP4 concatenado.
     Cache por id em MUSETALK_DIR/{id}.mp4 (notícia antiga não muda; poupa GPU).
  2. compose_presenter_block(): o /generate devolve um CLOSE QUADRADO (~572px),
     não uma cena. Então o fundo do estúdio (studio_bg_novo.png) é a BASE
     1920x1080 e o MP4 do MuseTalk entra em ESCALA NATIVA (sem upscale) à
     direita, com um fino contorno opcional (não é green-screen -> quadro de
     borda reta). Por cima: logo + lower third (com a manchete) + ticker
     rolante. O áudio é a trilha do próprio MP4. NÃO usa: avatar recortado,
     chroma key, TV b-roll, nem o mp3 da ElevenLabs. A música entra depois, no
     streamer, igual ao caminho D-ID.

     Posição/contorno/fundo são env (sem rebuild): MUSETALK_PRESENTER_X/_Y,
     MUSETALK_PRESENTER_BORDER, MUSETALK_BG_NAME. Chroma-key real (opcional,
     desligado por padrão): MUSETALK_CHROMA_ENABLED/_COLOR/_SIMILARITY/_BLEND/
     _DESPILL — mesma sintaxe do caminho D-ID (renderer/main.py).

REQUISITOS (ver lib/gpu_client.py): GPU_LIPSYNC_REAL=true, GPU_SERVER_URL,
INFERENCE_SERVER_API_KEY. GENERATE_TIMEOUT_SEC opcional (piso 1800s).

Qualquer falha -> RuntimeError com contexto. Diferente do D-ID, aqui NÃO há
fallback estático: o MuseTalk É o provedor; quem chama decide o que fazer.
"""

import json
import os
import re
import subprocess
import sys
import time

TAG = "MuseTalk"

# lib/gpu_client.py está na RAIZ do repo. O build atual do renderer
# (build: ./renderer) não copia lib/ — adoção no 24/7 exige mudar o contexto de
# build (docker-compose.yml). Por ora o caminho testado é
# `python3 -m renderer.test_musetalk` na raiz do repo.
try:
    from lib.gpu_client import GpuError, generate_presenter_chunked, gpu_session
except ImportError:  # execução fora da raiz do repo
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from lib.gpu_client import GpuError, generate_presenter_chunked, gpu_session

MUSETALK_DIR = os.environ.get("MUSETALK_DIR", "/app/assets/musetalk")

# --- QA do áudio pós-geração (gate anti-"voz ininteligível") ---------------
# O pod devolve TTS + rosto num MP4 só; quando o TTS degenera (embola,
# trunca, fica mudo) o arquivo tem o tamanho "certo" e passava direto pro ar
# — foi o incidente de 04/09 (bloco de ~36 s no lugar de ~150 s). Este gate
# roda em get_presenter_video() ANTES de cachear/retornar. Reprova => o
# arquivo vai para MUSETALK_DIR/_rejected/ e quem chama (renderer) cai no
# caminho D-ID. Tudo afinável por env, sem rebuild:
#   MUSETALK_QA_ENABLED         mestre (default true). false = pula tudo.
#   MUSETALK_QA_CHARS_PER_SEC   ritmo de narração esperado (default 15;
#                               calibrado nos renders reais da ElevenLabs:
#                               ~13,7 e ~16,2 chars/s).
#   MUSETALK_QA_MIN_RATIO       dur_real / dur_esperada mínima (default 0.65
#                               => rejeita > ~1,5x rápido demais).
#   MUSETALK_QA_MAX_RATIO       máxima (default 3.0 => pega loop/trava).
#   MUSETALK_QA_MIN_SEC         piso absoluto de duração de áudio (default 3).
#   MUSETALK_QA_MAX_AV_SKEW_SEC defasagem vídeo x áudio tolerada (default 1.5).
#   MUSETALK_QA_SILENCE_DB      mean_volume abaixo disso = mudo (default -50).
#
# --- Chunking de TTS + lip-sync (lib/gpu_client.generate_presenter_chunked) --
# O Chatterbox não segmenta texto: comentário inteiro numa chamada de /tts
# trunca pra ~25 s ou crasha 500 (matriz 2026-09-05). E o proxy do RunPod
# corta /lipsync em ~100 s: um WAV de ~110 s dá HTTP 524 (2026-09-06). Por
# isso get_presenter_video() fatia TUDO por bloco de frase: /tts por bloco ->
# WAV concatenado (QA aqui) -> /lipsync por bloco -> MP4s concatenados.
#   MUSETALK_TTS_MAX_CHARS      chars por bloco (default 350; a matriz mostrou
#                               100% completo até ~450, teto em 40 s acima).
#                               ~350 ch ~= ~25 s de áudio -> cabe no /lipsync.
def _qa_bool(name, default):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _qa_float(name, default):
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


QA_ENABLED = _qa_bool("MUSETALK_QA_ENABLED", True)
QA_CHARS_PER_SEC = _qa_float("MUSETALK_QA_CHARS_PER_SEC", 15.0)
QA_MIN_RATIO = _qa_float("MUSETALK_QA_MIN_RATIO", 0.65)
QA_MAX_RATIO = _qa_float("MUSETALK_QA_MAX_RATIO", 3.0)
QA_MIN_SEC = _qa_float("MUSETALK_QA_MIN_SEC", 3.0)
QA_MAX_AV_SKEW_SEC = _qa_float("MUSETALK_QA_MAX_AV_SKEW_SEC", 1.5)
QA_SILENCE_DB = _qa_float("MUSETALK_QA_SILENCE_DB", -50.0)

REJECTED_DIRNAME = "_rejected"

# --- Fundo + posição do quadro do apresentador (env, sem rebuild) -----------
# O MuseTalk devolve um close quadrado (~572px). Ele entra em escala NATIVA
# sobre o fundo abaixo, ancorado em (X, Y). Escolhido no e2e 2026-09-06
# (frames A-F): X=1208 (âncora à direita), Y=336 (base tucada atrás do lower
# third, y>=800, escondendo a borda inferior reta do recorte), BORDER=false
# (o drawbox branco fazia parecer janela de chamada de vídeo).
BACKGROUND_NAME = os.environ.get("MUSETALK_BG_NAME", "studio_bg_novo.png")
PRESENTER_X = int(os.environ.get("MUSETALK_PRESENTER_X", "1208"))
PRESENTER_Y = int(os.environ.get("MUSETALK_PRESENTER_Y", "336"))
# Escala do recorte antes do overlay. 1.0 = nativo (~572px, close de rosto —
# ~1,4x mais largo que o apresentador D-ID). < 1.0 encolhe pra aproximar da
# proporção do D-ID e parecer sentado (ajustar Y junto).
try:
    PRESENTER_SCALE = float(os.environ.get("MUSETALK_PRESENTER_SCALE", "1.0"))
except ValueError:
    PRESENTER_SCALE = 1.0
PRESENTER_BORDER = os.environ.get("MUSETALK_PRESENTER_BORDER", "false").strip().lower() in (
    "1", "true", "yes", "on",
)

# --- Bancada/console em primeiro plano (studio_bg_novo.png não tem mesa) ----
# PNG RGBA de canvas inteiro (1920x1080, ver assets/make_studio_desk.py),
# compositado ENTRE o apresentador e o logo — ancora visualmente a figura e
# esconde a borda inferior do recorte, como a bancada da cena antiga. Mesmo
# asset/vars valem pro caminho D-ID (renderer/main.py). Desligado por padrão.
#   STUDIO_DESK_ENABLED   liga/desliga (default false)
#   STUDIO_DESK_IMG       nome do arquivo em assets/ (default studio_desk.png)
#   STUDIO_DESK_Y         deslocamento vertical do overlay (default 0)
DESK_ENABLED = os.environ.get("STUDIO_DESK_ENABLED", "false").strip().lower() in (
    "1", "true", "yes", "on",
)
DESK_NAME = os.environ.get("STUDIO_DESK_IMG", "studio_desk.png").strip()
try:
    DESK_Y = int(os.environ.get("STUDIO_DESK_Y", "0"))
except ValueError:
    DESK_Y = 0

# --- Chroma key do vídeo do apresentador (MuseTalk) -------------------------
# O idle novo (LivePortrait) tem fundo verde-chroma limpo e estável. Mesma
# sintaxe do caminho D-ID (renderer/main.py): chromakey=COLOR:SIMILARITY:BLEND
# + despill opcional. MUSETALK_CHROMA_ENABLED é a chave-mestra — "false"
# (padrão) mantém a composição "caixa nativa" atual byte-idêntica; só liga
# depois de calibrar COLOR/SIMILARITY/BLEND contra um frame real do idle novo.
MUSETALK_CHROMA_ENABLED = os.environ.get("MUSETALK_CHROMA_ENABLED", "false").strip().lower() in (
    "1", "true", "yes", "on",
)
MUSETALK_CHROMA_COLOR = os.environ.get("MUSETALK_CHROMA_COLOR", "0x00B140").strip()
MUSETALK_CHROMA_SIMILARITY = os.environ.get("MUSETALK_CHROMA_SIMILARITY", "0.14").strip()
MUSETALK_CHROMA_BLEND = os.environ.get("MUSETALK_CHROMA_BLEND", "0.06").strip()
MUSETALK_CHROMA_DESPILL = os.environ.get("MUSETALK_CHROMA_DESPILL", "true").strip().lower() in (
    "1", "true", "yes", "on",
)

# --- Geometria do lower third ------------------------------------------------
# ESPELHA renderer/main.py (mesma caixa do lowerthird.png @ 1920x200, y=800).
# Mantido em paralelo DE PROPÓSITO: assim o caminho D-ID em main.py fica
# byte-identical. Se um dia divergir, realinhar os dois.
LT_TEXT_X = 218
LT_BOX_RIGHT = 1702
LT_MAX_TEXT_PX = LT_BOX_RIGHT - LT_TEXT_X
LT_BASE_FONTSIZE = 44
LT_MIN_FONTSIZE = 26
LT_BOX_CENTER_Y = 901
LT_GLYPH_RATIO = 0.55

PROMO_LT_FONTSIZE = 46
PROMO_SEAL_FONTSIZE = 26
_PROMO_MAIN_LINE_H = int(PROMO_LT_FONTSIZE * 1.25)
_PROMO_SEAL_LINE_H = int(PROMO_SEAL_FONTSIZE * 1.25)
PROMO_LT_MAIN_Y = int(
    LT_BOX_CENTER_Y - (_PROMO_MAIN_LINE_H + _PROMO_SEAL_LINE_H) / 2
) + 6
PROMO_SEAL_Y = PROMO_LT_MAIN_Y + _PROMO_MAIN_LINE_H
PROMO_LOWERTHIRD = "youtube.com/@FutureVerse-Beyond"


def _approx_text_width(text, fontsize):
    return len(text) * LT_GLYPH_RATIO * fontsize


def _truncate_to_width(text, fontsize, max_px):
    if _approx_text_width(text, fontsize) <= max_px:
        return text
    max_chars = max(1, int(max_px / (LT_GLYPH_RATIO * fontsize)) - 1)
    return text[:max_chars].rstrip() + "…"


def layout_lowerthird(title):
    """(texto, fontsize, y) p/ a manchete não vazar da caixa. Espelha
    renderer/main.py:layout_lowerthird."""
    title = " ".join((title or "").split())

    def _y_for(fontsize, n_lines):
        line_h = int(fontsize * 1.25)
        return int(LT_BOX_CENTER_Y - (n_lines * line_h) / 2)

    if not title:
        return "", LT_BASE_FONTSIZE, _y_for(LT_BASE_FONTSIZE, 1)
    if _approx_text_width(title, LT_BASE_FONTSIZE) <= LT_MAX_TEXT_PX:
        return title, LT_BASE_FONTSIZE, _y_for(LT_BASE_FONTSIZE, 1)
    mid = len(title) // 2
    left = title.rfind(" ", 0, mid)
    right = title.find(" ", mid)
    cands = [p for p in (left, right) if p != -1]
    if cands:
        split = min(cands, key=lambda p: abs(p - mid))
        line1, line2 = title[:split].strip(), title[split:].strip()
    else:
        line1, line2 = title, ""
    longest = max(len(line1), len(line2) if line2 else 0)
    fontsize = LT_BASE_FONTSIZE
    while fontsize > LT_MIN_FONTSIZE and longest * LT_GLYPH_RATIO * fontsize > LT_MAX_TEXT_PX:
        fontsize -= 2
    line1 = _truncate_to_width(line1, fontsize, LT_MAX_TEXT_PX)
    if line2:
        line2 = _truncate_to_width(line2, fontsize, LT_MAX_TEXT_PX)
    text = f"{line1}\n{line2}" if line2 else line1
    return text, fontsize, _y_for(fontsize, 2 if line2 else 1)


def get_presenter_video(news_id, text, *, out_dir=None):
    """
    MP4 (rosto + voz) do apresentador via lib/gpu_client.generate_presenter_chunked:
    texto -> blocos de frase -> /tts por bloco -> WAV concatenado -> QA no WAV
    -> /lipsync por bloco -> MP4s concatenados -> QA no MP4. Cache por id:
    {out_dir}/{news_id}.mp4 já existente e > 0 bytes é reusado (0 GPU). Levanta
    RuntimeError em falha.
    """
    out_dir = out_dir or MUSETALK_DIR
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{news_id}.mp4")

    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        try:
            _validate_presenter_media(out_path, text)
            print(f"{TAG} → Cache HIT para {news_id}: {out_path}", flush=True)
            return out_path
        except RuntimeError as e:
            _reject_media(out_path, out_dir, news_id, f"cache reprovado no QA — {e}")
            print(f"{TAG} → cache de {news_id} reprovado; regenerando.", flush=True)

    if not text or not str(text).strip():
        raise RuntimeError(f"texto vazio para {news_id}; nada a gerar.")

    print(
        f"{TAG} → Gerando apresentador para {news_id} ({len(str(text))} chars) "
        f"— TTS + lip-sync com chunking no pod GPU...",
        flush=True,
    )
    wav_tmp = os.path.join(out_dir, f".{news_id}.tts.wav")
    have_video = False

    def _qa_wav(wav):
        # QA no áudio concatenado ANTES do lip-sync — pega TTS ruim cedo, sem
        # gastar GPU no lip-sync de um áudio já reprovado. Roda dentro de
        # generate_presenter_chunked(); levantar aqui aborta o pipeline.
        try:
            _validate_presenter_media(wav, text)
        except RuntimeError as e:
            _reject_media(wav, out_dir, news_id, f"TTS reprovada no QA — {e}")
            raise RuntimeError(
                f"TTS de {news_id} reprovada no QA de áudio — {e}"
            ) from e

    try:
        with gpu_session() as pod_url:
            # Pipeline chunked: texto -> blocos de frase (<= MUSETALK_TTS_MAX_CHARS)
            # -> /tts por bloco -> WAV concatenado (wav_tmp) -> _qa_wav -> /lipsync
            # por bloco -> MP4 concatenado (out_path). Fatiar o lip-sync também é
            # obrigatório: o proxy do RunPod corta /lipsync em ~100 s, e um
            # comentário inteiro (~110 s de áudio) dá HTTP 524 numa chamada só.
            generate_presenter_chunked(
                str(text), out_path=out_path, wav_out=wav_tmp,
                on_wav=_qa_wav, server_url=pod_url,
            )
            have_video = os.path.isfile(out_path) and os.path.getsize(out_path) > 0
    except GpuError as e:
        if have_video:
            # Vídeo pronto; só o desligamento do pod falhou. Não jogamos fora,
            # mas gritamos alto — o pod pode ter ficado ligado cobrando.
            print(
                f"{TAG} → ⚠️ ATENÇÃO: vídeo gerado OK mas o ciclo do pod falhou "
                f"({e}) — CONFIRA NO RUNPOD SE O POD FICOU LIGADO.",
                flush=True,
            )
        else:
            raise RuntimeError(f"ciclo do pod GPU falhou para {news_id}: {e}") from e
    finally:
        try:
            os.remove(wav_tmp)
        except OSError:
            pass

    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError(f"pipeline não produziu MP4 válido para {news_id}: {out_path}")

    # QA final no MP4 concatenado (redundante com o QA do WAV, mas pega
    # mux/lip-sync torto — defasagem A/V, junção de blocos ruim, etc.).
    try:
        _validate_presenter_media(out_path, text)
    except RuntimeError as e:
        _reject_media(out_path, out_dir, news_id, str(e))
        raise RuntimeError(
            f"vídeo do apresentador para {news_id} reprovado no QA — {e}"
        ) from e

    print(f"{TAG} → apresentador pronto: {out_path} ({os.path.getsize(out_path)} bytes)", flush=True)
    return out_path


def _probe_dims(path):
    """(w, h) do 1º stream de vídeo via ffprobe; (None, None) se falhar."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30,
        )
        w, h = out.stdout.strip().split("x")
        return int(w), int(h)
    except Exception:
        return None, None


def _probe_media(path):
    """Sonda ffprobe -> dict {has_audio, audio_dur, video_dur, sample_rate}.
    Campos que não deram para ler ficam None/False. `format.duration` serve de
    fallback quando a duração por stream vem 'N/A'."""
    info = {"has_audio": False, "audio_dur": None, "video_dur": None, "sample_rate": None}
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-of", "json",
             "-show_entries", "stream=codec_type,duration,sample_rate:format=duration", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30,
        )
        data = json.loads(res.stdout or "{}")
    except Exception:
        return info

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    fmt_dur = _f((data.get("format") or {}).get("duration"))
    for st in data.get("streams", []):
        ct = st.get("codec_type")
        dur = _f(st.get("duration")) or fmt_dur
        if ct == "audio":
            info["has_audio"] = True
            info["audio_dur"] = dur
            sr = st.get("sample_rate")
            try:
                info["sample_rate"] = int(sr) if sr and sr != "N/A" else None
            except ValueError:
                info["sample_rate"] = None
        elif ct == "video":
            info["video_dur"] = dur
    return info


def _mean_volume_db(path):
    """mean_volume (dB) via ffmpeg volumedetect; None se não der para medir."""
    try:
        res = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
             "-map", "0:a", "-af", "volumedetect", "-f", "null", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120,
        )
    except Exception:
        return None
    m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", res.stderr or "")
    return float(m.group(1)) if m else None


def _reject_media(path, out_dir, news_id, reason):
    """Move um MP4 reprovado para {out_dir}/_rejected/{id}-{ts}.mp4 (evidência +
    fixture de regressão) e loga. Nunca levanta — quem chama decide o resto."""
    try:
        rej_dir = os.path.join(out_dir, REJECTED_DIRNAME)
        os.makedirs(rej_dir, exist_ok=True)
        dest = os.path.join(
            rej_dir, f"{news_id}-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}.mp4"
        )
        os.replace(path, dest)
        print(f"{TAG} → ⚠️ mídia reprovada -> {dest} (motivo: {reason})", flush=True)
    except OSError as e:
        print(f"{TAG} → AVISO: não movi a mídia reprovada {path!r} ({e}); apagando.", flush=True)
        try:
            os.remove(path)
        except OSError:
            pass


def _validate_presenter_media(mp4_path, text):
    """Gate de QA do MP4 (voz + rosto) que volta do pod. Reprova (RuntimeError)
    quando o áudio é curto/embolado/mudo demais para o texto — o sintoma do
    incidente de 04/09. Passa em silêncio (só loga) quando está OK.

    MUSETALK_QA_ENABLED=false pula tudo."""
    name = os.path.basename(mp4_path)
    if not QA_ENABLED:
        print(f"{TAG} → QA de áudio DESLIGADO (MUSETALK_QA_ENABLED=false); pulando {name}.", flush=True)
        return

    info = _probe_media(mp4_path)
    if not info["has_audio"]:
        raise RuntimeError(f"{name}: sem stream de áudio.")

    dur_a = info["audio_dur"] or 0.0
    dur_v = info["video_dur"] or 0.0
    clean = " ".join((text or "").split())
    expected = len(clean) / QA_CHARS_PER_SEC if clean else 0.0

    if dur_a < QA_MIN_SEC:
        raise RuntimeError(
            f"{name}: áudio de {dur_a:.1f}s abaixo do piso ({QA_MIN_SEC:.0f}s)."
        )

    if expected > 0:
        ratio = dur_a / expected
        if ratio < QA_MIN_RATIO:
            raise RuntimeError(
                f"{name}: áudio de {dur_a:.1f}s para ~{len(clean)} caracteres "
                f"(esperado ~{expected:.0f}s; razão {ratio:.2f} < {QA_MIN_RATIO}) "
                f"— TTS embolada/truncada."
            )
        if ratio > QA_MAX_RATIO:
            raise RuntimeError(
                f"{name}: áudio de {dur_a:.1f}s para ~{expected:.0f}s esperados "
                f"(razão {ratio:.2f} > {QA_MAX_RATIO}) — TTS travada/em loop."
            )

    if dur_v and abs(dur_v - dur_a) > QA_MAX_AV_SKEW_SEC:
        raise RuntimeError(
            f"{name}: vídeo {dur_v:.1f}s x áudio {dur_a:.1f}s "
            f"(defasagem > {QA_MAX_AV_SKEW_SEC}s)."
        )

    mv = _mean_volume_db(mp4_path)
    if mv is not None and mv < QA_SILENCE_DB:
        raise RuntimeError(
            f"{name}: áudio praticamente mudo (mean_volume {mv:.1f} dB < {QA_SILENCE_DB} dB)."
        )

    print(
        f"{TAG} → QA de áudio OK ({name}): áudio={dur_a:.1f}s vídeo={dur_v:.1f}s "
        f"esperado~{expected:.0f}s mean_vol={'?' if mv is None else round(mv, 1)}dB "
        f"sr={info['sample_rate']}Hz",
        flush=True,
    )


def compose_presenter_block(news_id, text, title, category, *, assets_dir,
                            ticker_dir, out_path, ticker_text=None,
                            presenter_mp4=None, background_img=None):
    """
    Gera `out_path` = fundo do estúdio (BASE 1920x1080) + MP4 do MuseTalk em
    ESCALA NATIVA à direita (contorno fino opcional) + logo + lower third
    (manchete) + ticker rolante. Áudio = trilha do próprio MP4 do MuseTalk.

    presenter_mp4: None => chama get_presenter_video() (cache + GPU). Passe um
    caminho para só recompor os gráficos, sem novo gasto de GPU.
    background_img: None => {assets_dir}/{MUSETALK_BG_NAME} (studio_bg_novo.png).
    Levanta RuntimeError em qualquer falha (gpu_client ou ffmpeg).
    """
    is_promo = (category or "").strip().lower() in ("promoção", "promocao", "promo")
    if presenter_mp4 is None:
        presenter_mp4 = get_presenter_video(news_id, text)
    if not os.path.exists(presenter_mp4) or os.path.getsize(presenter_mp4) == 0:
        raise RuntimeError(f"MP4 do apresentador ausente/vazio: {presenter_mp4!r}")

    background_img = background_img or os.path.join(assets_dir, BACKGROUND_NAME)
    logo_img = os.path.join(assets_dir, "logo.png")
    lowerthird_img = os.path.join(assets_dir, "lowerthird.png")
    ticker_img = os.path.join(ticker_dir, "ticker.png")
    for p in (background_img, logo_img, lowerthird_img, ticker_img):
        if not os.path.exists(p):
            raise RuntimeError(f"asset não encontrado: {p}")

    pv_w, pv_h = _probe_dims(presenter_mp4)
    # Escala do recorte (MUSETALK_PRESENTER_SCALE): 1.0 = nativo. Dimensões
    # pares (libx264). pv_w/pv_h passam a ser as JÁ escaladas (usadas no
    # contorno opcional). Sem scale se == 1.0 (byte-idêntico ao de antes).
    _sc_filter = ""
    if abs(PRESENTER_SCALE - 1.0) > 1e-3 and pv_w and pv_h:
        pv_w = max(2, (int(pv_w * PRESENTER_SCALE) // 2) * 2)
        pv_h = max(2, (int(pv_h * PRESENTER_SCALE) // 2) * 2)
        _sc_filter = f",scale={pv_w}:{pv_h}"  # prefixado com ',' pra encaixar após setsar=1

    tmp_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    title_txt = os.path.join(tmp_dir, f".musetalk_title_{news_id}.txt")
    ticker_txt = os.path.join(tmp_dir, f".musetalk_ticker_{news_id}.txt")

    if is_promo:
        lt_text, lt_fontsize, lt_y = PROMO_LOWERTHIRD, PROMO_LT_FONTSIZE, PROMO_LT_MAIN_Y
    else:
        lt_text, lt_fontsize, lt_y = layout_lowerthird(title or "")
    tk_text = ticker_text if ticker_text is not None else (title or "")

    with open(title_txt, "w", encoding="utf-8") as f:
        f.write(lt_text)
    with open(ticker_txt, "w", encoding="utf-8") as f:
        f.write(tk_text)

    logo_scale = "300:113" if is_promo else "220:83"
    logo_xy = "40:24" if is_promo else "40:30"
    lt_fontcolor = "0x00E0FF" if is_promo else "white"

    # Entradas: 0=apresentador(vídeo+áudio) 1=fundo 2=logo 3=lowerthird 4=ticker
    if MUSETALK_CHROMA_ENABLED:
        despill = ",despill=type=green:mix=0.5:expand=0" if MUSETALK_CHROMA_DESPILL else ""
        presenter_filter = (
            f"[0:v]setsar=1{_sc_filter},"
            f"chromakey={MUSETALK_CHROMA_COLOR}:{MUSETALK_CHROMA_SIMILARITY}:{MUSETALK_CHROMA_BLEND}"
            f"{despill}[pv];"
        )
        print(
            f"{TAG} → chroma key no vídeo do apresentador: color={MUSETALK_CHROMA_COLOR} "
            f"similarity={MUSETALK_CHROMA_SIMILARITY} blend={MUSETALK_CHROMA_BLEND} "
            f"despill={'on' if MUSETALK_CHROMA_DESPILL else 'off'}",
            flush=True,
        )
    else:
        presenter_filter = f"[0:v]setsar=1{_sc_filter}[pv];"

    fc = (
        "[1:v]scale=1920:1080:force_original_aspect_ratio=increase,"
        "crop=1920:1080,setsar=1[bg];"
        f"{presenter_filter}"
        f"[bg][pv]overlay={PRESENTER_X}:{PRESENTER_Y}[b0];"
    )
    last = "b0"
    # Contorno branco só faz sentido pra disfarçar a borda reta do quadro SEM
    # chroma. Com chroma real ligado, o recorte já não é um retângulo opaco —
    # desliga o drawbox nesse caso, independente de PRESENTER_BORDER.
    if PRESENTER_BORDER and not MUSETALK_CHROMA_ENABLED and pv_w and pv_h:
        fc += (
            f"[{last}]drawbox=x={PRESENTER_X - 2}:y={PRESENTER_Y - 2}:"
            f"w={pv_w + 4}:h={pv_h + 4}:color=white@0.5:t=2[b0b];"
        )
        last = "b0b"
    # Bancada em primeiro plano (entre apresentador e logo). Input 5.
    desk_img = os.path.join(assets_dir, DESK_NAME)
    use_desk = DESK_ENABLED and os.path.exists(desk_img)
    if use_desk:
        fc += f"[5:v]setsar=1[dk];[{last}][dk]overlay=0:{DESK_Y}[b0d];"
        last = "b0d"

    fc += (
        f"[2:v]scale={logo_scale}[lg];[{last}][lg]overlay={logo_xy}[b1];"
        "[3:v]scale=1920:200[lt];[b1][lt]overlay=0:800[b2];"
        f"[b2]drawtext=textfile='{title_txt}':fontcolor={lt_fontcolor}:"
        f"fontsize={lt_fontsize}:x={LT_TEXT_X}:y={lt_y}[b3];"
        "[4:v]scale=1920:80[tk];[b3][tk]overlay=0:1000[b4];"
    )
    last = "b4"
    if is_promo:
        fc += (
            "[b4]drawtext=text='INSCREVA-SE  •  DEIXE SEU LIKE  •  ATIVE O SININHO':"
            f"fontcolor=0x00E0FF:fontsize={PROMO_SEAL_FONTSIZE}:x={LT_TEXT_X}:y={PROMO_SEAL_Y}[b5];"
        )
        last = "b5"
    fc += (
        f"[{last}]drawtext=textfile='{ticker_txt}':fontcolor=white:fontsize=28:"
        r"x=w-mod(t*160\,w+tw):y=1018[vout]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", presenter_mp4,
        "-loop", "1", "-i", background_img,
        "-loop", "1", "-i", logo_img,
        "-loop", "1", "-i", lowerthird_img,
        "-loop", "1", "-i", ticker_img,
        *(["-loop", "1", "-i", desk_img] if use_desk else []),
        "-filter_complex", fc,
        "-map", "[vout]", "-map", "0:a",
        "-r", "30",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        out_path,
    ]
    print(f"{TAG} → compondo bloco (musetalk) → {out_path}", flush=True)
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for t in (title_txt, ticker_txt):
        try:
            os.remove(t)
        except OSError:
            pass
    if res.returncode != 0:
        raise RuntimeError(
            f"ffmpeg falhou (rc={res.returncode}) para {news_id}:\n{res.stderr[-2000:]}"
        )
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError(f"{out_path} não foi gerado para {news_id}")
    print(f"{TAG} → bloco pronto: {out_path} ({os.path.getsize(out_path)} bytes)", flush=True)
    return out_path
