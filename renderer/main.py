import time
import redis
import subprocess
import os
import json

# Conexão estável com Redis
r = redis.Redis(host="redis", port=6379, decode_responses=True, socket_timeout=10)
last_id = "0-0"

# Definições de Diretórios Internos do Container
OUTPUT_DIR = "/app/output"
TICKER_DIR = "/app/ticker"
ASSETS_DIR = "/app/assets"

# Garantir Diretórios em Disco
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TICKER_DIR, exist_ok=True)

# Arquivos de Mídia Mapeados
TEMPLATE = f"{ASSETS_DIR}/base.mp4"
DUMMY_AUDIO = f"{ASSETS_DIR}/news_audio.wav"
TICKER_IMG = f"{TICKER_DIR}/ticker.png"

# Destinos Finais
final_file = f"{OUTPUT_DIR}/final.mp4"
final_temp = f"{OUTPUT_DIR}/final_temp.mp4"
title_txt_file = f"{OUTPUT_DIR}/title_temp.txt"

print("Renderer 2D → Motor Gráfico Online. Aguardando news.ready...")

while True:
    try:
        msgs = r.xread({"news.ready": last_id}, block=5000, count=1)
        if not msgs:
            continue

        for stream, events in msgs:
            for event_id, data in events:
                last_id = event_id

                title = data.get("title", "Sem Título")
                category = data.get("category", "Geral")
                text = data.get("commentary", "")
                news_id = data.get("id", "0")

                print(f"Renderer → Processando Notícia ID {news_id}: {title}")

                # Escreve a manchete no arquivo temporário blindado contra aspas
                with open(title_txt_file, "w", encoding="utf-8") as f:
                    f.write(title)

                # COMANDO PROFISSIONAL: Vídeo Base Real + Áudio + Ticker com Filtros Encadeados
                cmd = [
                    "ffmpeg", "-y",
                    "-i", TEMPLATE,
                    "-i", DUMMY_AUDIO,
                    "-i", TICKER_IMG,
                    "-filter_complex", f"[0:v]drawtext=textfile='{title_txt_file}':fontcolor=white:fontsize=36:x=50:y=50[vtext];[vtext][2:v]overlay=0:H-90[vout]",
                    "-map", "[vout]",
                    "-map", "1:a",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-t", "3",
                    final_temp
                ]

                print("Renderer → Renderizando Bloco Gráfico Unificado...")
                result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

                if os.path.exists(title_txt_file):
                    os.remove(title_txt_file)

                if result.returncode != 0:
                    print("Renderer → ERRO CRÍTICO NO FFMPEG:")
                    print(result.stderr)
                    continue

                # Entrega Atômica e Definitiva para o Streamer
                if os.path.exists(final_temp):
                    os.replace(final_temp, final_file)
                    os.chmod(final_file, 0o777)
                    print(f"Renderer → SUCESSO EMISSÃO: {final_file} gerado com sucesso!")
                else:
                    print("Renderer → ERRO: final_temp.mp4 não foi encontrado.")
                    continue

                block = {
                    "id": news_id,
                    "title": title,
                    "category": category,
                    "text": text,
                    "video_file": final_file
                }
                r.xadd("news.block", block)

    except Exception as e:
        print("Renderer MAIN LOOP ERROR:", e)
        time.sleep(2)
