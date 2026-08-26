class UltraFastClient:
    def __init__(self):
        # aqui você conecta no Ultrafast real (HTTP, gRPC, etc.)
        pass

    def enhance_news(self, title: str, summary: str, category: str):
        # por enquanto, só padroniza minimamente
        improved_title = title.strip()
        improved_summary = summary.strip()
        improved_category = category.strip()

        return {
            "title": improved_title,
            "summary": improved_summary,
            "category": improved_category,
        }
