import redis
import time
import json
from datetime import datetime

r = redis.Redis(host="redis", decode_responses=True)

last_id = "0-0"

print("Streamer iniciado. Montando programas...")

# Buffer de blocos para montar uma edição
current_blocks = []
PROGRAM_BLOCK_LIMIT = 10  # quantidade de notícias por programa

def build_program(blocks):
    if not blocks:
        return None

    program_id = f"fvnews-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"

    # abertura padrão
    opening = {
        "type": "opening",
        "vignette": "futureverse_vinheta_3s",
        "signature": "futureverse_signature_0_9s"
    }

    # encerramento padrão
    closing = {
        "type": "closing",
        "vignette": "futureverse_vinheta_3s",
        "signature": "futureverse_signature_0_9s"
    }

    # playlist de blocos
    playlist = []

    for idx, block in enumerate(blocks, start=1):
        playlist.append({
            "position": idx,
            "id": block["id"],
            "title": block["title"],
            "category": block["category"],
            "tone_profile": block.get("tone_profile"),
            "fx_package": block.get("fx_package"),
            "transition": block.get("transition"),
            "block_structure": block.get("block_structure", [])
        })

    program = {
        "program_id": program_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "opening": opening,
        "closing": closing,
        "blocks": playlist,
        "total_blocks": len(blocks)
    }

    return program

while True:
    msgs = r.xread({"news.block": last_id}, block=1000, count=1)

    if msgs:
        stream, entries = msgs[0]

        for entry_id, data in entries:
            last_id = entry_id

            # adiciona bloco ao buffer
            current_blocks.append(data)
            print(f"Streamer → recebeu bloco: {data['title']} (total no buffer: {len(current_blocks)})")

            # quando atingir limite, monta programa
            if len(current_blocks) >= PROGRAM_BLOCK_LIMIT:
                program = build_program(current_blocks)
                if program:
                    r.xadd("news.program", {
                        "program_id": program["program_id"],
                        "created_at": program["created_at"],
                        "opening": json.dumps(program["opening"]),
                        "closing": json.dumps(program["closing"]),
                        "blocks": json.dumps(program["blocks"]),
                        "total_blocks": program["total_blocks"]
                    })
                    print(f"Streamer → news.program: {program['program_id']} com {program['total_blocks']} blocos")

                # limpa buffer para próxima edição
                current_blocks = []

    time.sleep(1)
