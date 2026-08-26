import time
import redis

r = redis.Redis(host="redis", port=6379, decode_responses=True)

def safe(v):
    if v is None:
        return ""
    return str(v)

last_id = "0-0"

while True:
    msgs = r.xread({"news.final": last_id}, block=1000, count=1)

    if msgs:
        stream, entries = msgs[0]
        entry_id, data = entries[0]
        last_id = entry_id

        audio = {
            "audio_file": "dummy_audio.wav",
            "text": safe(data.get("final_text")),
        }

        audio = {k: safe(v) for k, v in audio.items()}

        r.xadd("news.audio", audio)
        print("Presenter → news.audio:", audio)

    time.sleep(2)
