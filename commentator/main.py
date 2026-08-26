import time
import redis
import json

r = redis.Redis(host="redis", port=6379, decode_responses=True, socket_timeout=10)

def safe(v):
    if v is None:
        return ""
    return str(v)

def build_commentary(title, summary, category, script):
    commentary = []

    commentary.append(f"ANÁLISE: {title}")

    if category == "Politics":
        commentary.append(
            "Esta notícia revela movimentos importantes no cenário político, "
            "com possíveis impactos em decisões governamentais e relações institucionais."
        )
        commentary.append(
            "Historicamente, eventos desse tipo influenciam debates públicos e moldam a percepção da população."
        )

    elif category == "Economy":
        commentary.append(
            "O tema envolve fatores econômicos que podem afetar mercados, empregos e estabilidade financeira."
        )
        commentary.append(
            "Mudanças econômicas costumam gerar efeitos imediatos no custo de vida e nas políticas fiscais."
        )

    elif category == "Security":
        commentary.append(
            "Este episódio está ligado à segurança pública e pode gerar preocupação social significativa."
        )
        commentary.append(
            "Incidentes desse tipo frequentemente levam a revisões de protocolos e políticas de segurança."
        )

    elif category == "Climate":
        commentary.append(
            "A notícia destaca fenômenos climáticos ou ambientais que podem ter efeitos duradouros."
        )
        commentary.append(
            "Eventos climáticos extremos reforçam debates sobre sustentabilidade e preparação de comunidades."
        )

    elif category == "Health":
        commentary.append(
            "O assunto envolve saúde e bem-estar, temas que afetam diretamente a vida cotidiana."
        )
        commentary.append(
            "Questões de saúde pública costumam gerar discussões sobre prevenção, acesso e políticas sanitárias."
        )

    elif category == "Entertainment":
        commentary.append(
            "O foco está em cultura e entretenimento, refletindo tendências sociais e comportamentos do público."
        )
        commentary.append(
            "Produções culturais frequentemente influenciam debates sociais e moldam identidades coletivas."
        )

    elif category == "Sports":
        commentary.append(
            "A notícia envolve o universo esportivo, que mobiliza torcidas e movimenta grandes receitas."
        )
        commentary.append(
            "Eventos esportivos têm impacto direto em comunidades, clubes e na identidade cultural."
        )

    else:
        commentary.append(
            "Este acontecimento se insere no cenário internacional, com possíveis repercussões em diferentes regiões."
        )
        commentary.append(
            "Eventos globais costumam influenciar política, economia e relações diplomáticas."
        )

    commentary.append(
        "CONCLUSÃO: Seguiremos acompanhando os próximos desdobramentos e trazendo análises detalhadas conforme novas informações surgirem."
    )

    return "\n".join(commentary)

def main():
    last_id = "0-0"

    while True:
        try:
            msgs = r.xread({"news.classified": last_id}, block=5000, count=10)

            if not msgs:
                continue

            for stream, events in msgs:
                for event_id, data in events:
                    last_id = event_id

                    news_id = safe(data.get("id"))
                    title = safe(data.get("title"))
                    category = safe(data.get("category"))
                    script = safe(data.get("script"))
                    summary = safe(data.get("summary", ""))

                    commentary = build_commentary(title, summary, category, script)

                    out = {
                        "id": news_id,
                        "title": title,
                        "category": category,
                        "commentary": commentary,
                        "timestamp": safe(time.time())
                    }

                    r.xadd("news.final", out)
                    print(f"Commentator → {category}: {title}")

        except Exception as e:
            print("Commentator ERROR:", e)
            time.sleep(2)

if __name__ == "__main__":
    main()
