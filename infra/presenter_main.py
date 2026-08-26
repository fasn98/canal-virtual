import time
import redis

r = redis.Redis(host="redis", port=6379, decode_responses=True)

while True:
    msgs = r.xread({"news.final": "0-0"}, block=1000, count=1)

    if msgs:
        stream, entries = msgs[0]
        entry_id, data = entries[0]

        audio = {
            "audio_file": "dummy_audio.wav",
            "text": data.get("final_text")
        }

        r.xadd("news.audio", audio)
        print("Presenter → news.audio:", audio)

    time.sleep(2)
