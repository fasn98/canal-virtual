import time
import redis

r = redis.Redis(host="redis", port=6379, decode_responses=True)

def safe(v):
    if v is None:
        return ""
    return str(v)

last_id = "0-0"

while True:
    try:
        msgs = r.xread({"news.final": last_id}, block=1000, count=10)

        if msgs:
            stream, entries = msgs[0]

            for entry_id, data in entries:
                print("Presenter → RECEBEU:", entry_id, data)
                last_id = entry_id

                # texto final
                text_value = data.get("final_text") or data.get("commentary") or ""

                # ⭐ pacote completo para o Renderer
                audio = {
                    "id": safe(data.get("id")),
                    "title": safe(data.get("title")),
                    "category": safe(data.get("category")),
                    "timestamp": safe(data.get("timestamp")),
                    "audio_file": "dummy_audio.wav",  # depois trocamos para ElevenLabs
                    "text": safe(text_value),
                }

                r.xadd("news.audio", audio)
                print("Presenter → news.audio:", audio)

        time.sleep(1)

    except Exception as e:
        print("Presenter ERROR:", e)
        time.sleep(1)
