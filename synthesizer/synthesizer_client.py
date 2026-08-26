class SynthesizerClient:
    def __init__(self):
        pass

    def generate_layers(self, title: str, summary: str, category: str) -> dict:
        category = (category or "General").strip()

        # 1) Teaser — chamada curta
        script_teaser = self._build_teaser(title, summary, category)

        # 2) Texto do âncora
        script_anchor = self._build_anchor(title, summary, category)

        # 3) Comentário / análise
        script_commentary = self._build_commentary(title, summary, category)

        # 4) Contexto
        script_context = self._build_context(title, summary, category)

        # 5) Encerramento
        script_outro = self._build_outro(title, summary, category)

        # 6) Pacote de efeitos cognitivos
        fx_package = self._select_fx_package(category)

        # 7) Tipo de transição
        transition_type = self._select_transition(category)

        # 8) Tipo de vinheta
        vignette_type = "futureverse_vinheta_3s"

        # 9) Perfil de tom
        tone_profile = self._select_tone_profile(category)

        return {
            "title": title,
            "summary": summary,
            "category": category,

            "script_teaser": script_teaser,
            "script_anchor": script_anchor,
            "script_commentary": script_commentary,
            "script_context": script_context,
            "script_outro": script_outro,

            "fx_package": fx_package,
            "transition_type": transition_type,
            "vignette_type": vignette_type,
            "tone_profile": tone_profile,
        }

    # ------------------------
    # Camadas de script
    # ------------------------

    def _build_teaser(self, title: str, summary: str, category: str) -> str:
        base = title.strip()
        if category.lower() in ["world", "security", "climate"]:
            return f"{base}. Detalhes exclusivos agora no FutureVerse News."
        elif category.lower() in ["technology", "tech"]:
            return f"{base}. O futuro tecnológico em foco no FutureVerse News."
        elif category.lower() in ["economy"]:
            return f"{base}. Impactos econômicos explicados no FutureVerse News."
        elif category.lower() in ["politics"]:
            return f"{base}. Decisões políticas que moldam o futuro, aqui no FutureVerse News."
        elif category.lower() in ["entertainment", "culture"]:
            return f"{base}. Cultura e entretenimento em destaque no FutureVerse News."
        else:
            return f"{base}. As principais atualizações você acompanha no FutureVerse News."

    def _build_anchor(self, title: str, summary: str, category: str) -> str:
        summary = summary.strip()
        if category.lower() in ["world", "security", "climate"]:
            return (
                f"Boa noite. No FutureVerse News, acompanhamos agora: {title}. "
                f"{summary} "
                f"Seguimos monitorando os desdobramentos e trazendo uma visão clara do impacto global."
            )
        elif category.lower() in ["technology", "tech"]:
            return (
                f"No universo da tecnologia, o FutureVerse News destaca: {title}. "
                f"{summary} "
                f"Esta notícia se conecta diretamente com a forma como o futuro digital está sendo construído."
            )
        elif category.lower() in ["economy"]:
            return (
                f"Na esfera econômica, o FutureVerse News analisa: {title}. "
                f"{summary} "
                f"Vamos entender juntos como esses movimentos influenciam mercados e decisões estratégicas."
            )
        elif category.lower() in ["politics"]:
            return (
                f"Na política, o FutureVerse News traz: {title}. "
                f"{summary} "
                f"Essas decisões têm efeito direto na forma como sociedades se organizam e projetam o futuro."
            )
        elif category.lower() in ["entertainment", "culture"]:
            return (
                f"No campo da cultura e do entretenimento, o FutureVerse News apresenta: {title}. "
                f"{summary} "
                f"Essas histórias ajudam a entender como imaginamos e representamos o futuro."
            )
        else:
            return (
                f"O FutureVerse News acompanha: {title}. "
                f"{summary} "
                f"Seguimos conectando fatos, contexto e futuro em tempo real."
            )

    def _build_commentary(self, title: str, summary: str, category: str) -> str:
        if category.lower() in ["world", "security"]:
            return (
                f"Do ponto de vista geopolítico, {title} reforça a importância de observar padrões de risco, "
                f"resposta institucional e impacto sobre populações vulneráveis. "
                f"Eventos como este ajudam a calibrar a percepção de estabilidade e de preparação global."
            )
        elif category.lower() in ["climate"]:
            return (
                f"Em termos climáticos, {title} se encaixa em uma sequência de eventos extremos que vêm sendo "
                f"registrados nos últimos anos. "
                f"Isso alimenta debates sobre adaptação, mitigação e responsabilidade compartilhada entre nações."
            )
        elif category.lower() in ["technology", "tech"]:
            return (
                f"Na perspectiva tecnológica, {title} ilustra como inovação e risco caminham lado a lado. "
                f"Cada avanço traz novas possibilidades, mas também novos desafios éticos, regulatórios e sociais."
            )
        elif category.lower() in ["economy"]:
            return (
                f"Economicamente, {title} aponta para ajustes de expectativa, redistribuição de recursos e "
                f"reação de mercados. "
                f"Esses movimentos são fundamentais para entender tendências de médio e longo prazo."
            )
        elif category.lower() in ["politics"]:
            return (
                f"Politicamente, {title} mostra como decisões de liderança podem redefinir prioridades, "
                f"alianças e percepções públicas. "
                f"Esse tipo de notícia costuma ter efeitos que se estendem muito além do momento imediato."
            )
        elif category.lower() in ["entertainment", "culture"]:
            return (
                f"Culturalmente, {title} revela como narrativas, símbolos e personagens influenciam a forma "
                f"como enxergamos o presente e projetamos o futuro. "
                f"O entretenimento é um espelho poderoso das transformações sociais."
            )
        else:
            return (
                f"Em termos gerais, {title} contribui para o mosaico de informações que moldam nossa visão "
                f"do mundo. "
                f"Cada notícia adiciona uma peça ao quadro maior de como o futuro está sendo construído."
            )

    def _build_context(self, title: str, summary: str, category: str) -> str:
        if category.lower() in ["world", "security"]:
            return (
                f"Historicamente, eventos como {title} se conectam a ciclos de tensão, negociação e tentativa "
                f"de estabilização. "
                f"Observar o contexto regional e internacional ajuda a entender por que esses episódios ganham "
                f"tanta relevância."
            )
        elif category.lower() in ["climate"]:
            return (
                f"Do ponto de vista climático, notícias como {title} se somam a uma série de registros que "
                f"apontam para mudanças de padrão. "
                f"Relatórios científicos e dados de longo prazo são essenciais para interpretar esses sinais."
            )
        elif category.lower() in ["technology", "tech"]:
            return (
                f"No campo da tecnologia, {title} se insere em uma linha de evolução contínua, marcada por "
                f"disrupções e adaptações. "
                f"Comparar com casos anteriores ajuda a entender o ritmo e a direção dessas transformações."
            )
        elif category.lower() in ["economy"]:
            return (
                f"Em termos econômicos, {title} dialoga com ciclos de expansão, contração e reorganização de "
                f"cadeias produtivas. "
                f"Contextualizar com indicadores e decisões anteriores é fundamental para interpretar o impacto."
            )
        elif category.lower() in ["politics"]:
            return (
                f"Na política, {title} se conecta a uma sequência de decisões, disputas e acordos que moldam "
                f"instituições e políticas públicas. "
                f"Entender esse histórico ajuda a enxergar o que está em jogo em cada novo capítulo."
            )
        elif category.lower() in ["entertainment", "culture"]:
            return (
                f"No universo cultural, {title} se soma a obras, movimentos e tendências que refletem o espírito "
                f"do tempo. "
                f"Esse contexto é importante para perceber como a arte e o entretenimento respondem às mudanças sociais."
            )
        else:
            return (
                f"Contextualmente, {title} é parte de um cenário mais amplo de transformações sociais, econômicas "
                f"e tecnológicas. "
                f"Conectar essa notícia a outras recentes ajuda a construir uma visão mais integrada do momento atual."
            )

    def _build_outro(self, title: str, summary: str, category: str) -> str:
        if category.lower() in ["world", "security", "climate"]:
            return (
                f"O FutureVerse News segue acompanhando {title} e seus desdobramentos. "
                f"Você confere atualizações em tempo real, sempre com foco em clareza, contexto e futuro."
            )
        elif category.lower() in ["technology", "tech"]:
            return (
                f"Seguimos observando como notícias como {title} redefinem o cenário tecnológico. "
                f"No FutureVerse News, o futuro não é uma abstração — é algo que se constrói a cada novo fato."
            )
        elif category.lower() in ["economy"]:
            return (
                f"O FutureVerse News continua monitorando os impactos de {title} na economia global e local. "
                f"Nos próximos blocos, você vê como esses movimentos se conectam a outras tendências."
            )
        elif category.lower() in ["politics"]:
            return (
                f"Essa cobertura de {title} segue em evolução, e o FutureVerse News acompanha cada novo passo. "
                f"A política é um dos motores do futuro — e nós seguimos atentos a cada mudança de direção."
            )
        elif category.lower() in ["entertainment", "culture"]:
            return (
                f"O FutureVerse News continua trazendo histórias como {title}, que ajudam a entender como "
                f"imaginamos e representamos o futuro. "
                f"Nos próximos blocos, mais cultura, mais narrativa, mais futuro."
            )
        else:
            return (
                f"O FutureVerse News segue conectando notícias como {title} a uma visão mais ampla do mundo. "
                f"Nos próximos blocos, você acompanha outros fatos que ajudam a compor esse panorama."
            )

    # ------------------------
    # Efeitos, transições, tom
    # ------------------------

    def _select_fx_package(self, category: str) -> str:
        c = category.lower()
        if c in ["world"]:
            return "futureverse_fx_pulse_ciano"
        if c in ["security"]:
            return "futureverse_fx_fragment_holografico"
        if c in ["climate"]:
            return "futureverse_fx_particula_ascendente"
        if c in ["technology", "tech"]:
            return "futureverse_fx_line_draw"
        if c in ["economy"]:
            return "futureverse_fx_data_rise"
        if c in ["politics"]:
            return "futureverse_fx_sweep_orbital"
        if c in ["entertainment", "culture"]:
            return "futureverse_fx_nota_ciano"
        return "futureverse_fx_generic"

    def _select_transition(self, category: str) -> str:
        c = category.lower()
        if c in ["world", "security", "climate"]:
            return "futureverse_transition_pulse"
        if c in ["technology", "tech", "economy"]:
            return "futureverse_transition_particles"
        if c in ["politics", "entertainment", "culture"]:
            return "futureverse_transition_sweep"
        return "futureverse_transition_generic"

    def _select_tone_profile(self, category: str) -> str:
        c = category.lower()
        if c in ["world"]:
            return "world_serious"
        if c in ["security"]:
            return "security_alert"
        if c in ["climate"]:
            return "climate_urgent"
        if c in ["technology", "tech"]:
            return "tech_neutral"
        if c in ["economy"]:
            return "economy_analytical"
        if c in ["politics"]:
            return "politics_institutional"
        if c in ["entertainment", "culture"]:
            return "entertainment_light"
        return "general_neutral"
