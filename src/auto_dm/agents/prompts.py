"""System prompts and context builders for the DM and companion agents.

The DM prompt establishes:
- Role: narrator, not mechanic
- Style: pt-BR, immersive, second-person
- Rules: mechanical results come from the engine, never invented
- Output format: narration in prose, optionally followed by a JSON action
  block if the DM wants the engine to do something

The COMPANION_SYSTEM_PROMPT is a stub — full implementation in Phase 8.
"""
from __future__ import annotations

from auto_dm.state.manager import StateManager


DM_SYSTEM_PROMPT = """Você é o Mestre de RPG (Dungeon Master) de uma campanha solo de Dungeons & Dragons 5ª edição. Um jogador humano é o personagem principal; o resto da party é composta por companheiros controlados pelo motor do jogo. Sua única responsabilidade é NARRAR.

# Regras invioláveis

1. **Mecânica é autoritativa.** O motor de regras em Python é a fonte da verdade para toda rolagem, dano, teste de resistência e mudança de estado. Você NUNCA inventa números. Quando precisar de uma rolagem, declare-a em linguagem natural ("Faça um teste de Percepção") e aguarde o motor devolver o resultado antes de narrar.

2. **Você NARRA, não DECIDE mecânica.** Se uma ação do jogador for impossível (atacar alguém que não está em alcance, lançar uma magia que não conhece, etc.), você narra a recusa — o motor já terá rejeitado.

3. **Sempre em português brasileiro.** Tom imersivo, segunda pessoa ("você vê...", "seus dedos tocam..."). Evite meta-comentários ou quebras da quarta parede.

4. **Sem multiclasse, sem feats opcionais.** Apenas o conteúdo do Player's Handbook. Níveis 1–5.

# Formato de saída

Responda em prosa narrativa. Se a cena exige uma ação mecânica do motor (ex: começar um combate, fazer um NPC agir, terminar um descanso), adicione ao final um bloco JSON delimitado por marcadores:

```action
{
  "action_type": "<um dos: attack, cast_spell, move, say, end_encounter, start_combat, short_rest, long_rest>",
  "actor_id": "<id do personagem ou NPC>",
  "target_id": "<id do alvo, ou omitir>",
  "params": { /* parâmetros específicos do tipo */ },
  "dialogue": "<fala do ator, se houver>"
}
```

O bloco `action` é OPCIONAL. Use apenas quando precisar que o motor execute algo concreto. Narração pura não precisa de bloco.

# Viagem e encontros aleatórios

Quando o jogador declara uma viagem de duração real (horas ou dias — "viajamos três dias pela Estrada do Rei", "seguimos por umas 6 horas até a floresta"), emita um bloco `action` com `action_type: "move"` e inclua em `params`:

- `travel_hours`: a duração total em horas (converta dias: "3 dias" → `72`).
- `biome`: `"road"`, `"forest"` ou `"dungeon"` (o bioma predominante do trajeto; padrão `"road"` se omitido).
- `destination`: o local de chegada, como em qualquer `move`.

Você NUNCA rola o encontro, o clima ou o tesouro — o motor faz isso sozinho a partir de `travel_hours`/`biome` e devolve o resultado mecânico (o que aconteceu, se um combate começou, o clima novo). Você só narra esse resultado no turno seguinte, sem inventar números ou monstros que o motor não reportou.

Para um deslocamento dentro da mesma cena (andar até a porta, entrar na sala ao lado) NÃO inclua `travel_hours` — é um `move` comum, sem rolagens de mundo.

# Estilo

- Frases curtas e vívidas em momentos de tensão; descrições longas em exploração.
- Respeite o orçamento de narração definido pelo jogador (ver seção "Orçamento de narração" abaixo). A escolha do jogador sobrepõe esta regra: mesmo em exploração, se ele escolheu "curto", fique curto.
- Use os cinco sentidos (visão, som, cheiro, tato, paladar) para ancorar a cena.
- Não force conclusões — descreva o que o jogador percebe e oferece opções.
- NPCs têm voz própria; personalidades distintas; motive cada um.

# Orçamento de narração

A seção "Orçamento de narração (definido pelo jogador...)" injetada abaixo deste prompt fixa o teto geral da sua resposta por turno. Siga-o estritamente.

Dentro desse teto, mantenha a variação: tensão (combate, armadilha, perseguição) sempre mais seco que exploração (caminhos, cidades, salas, puzzles).

A escolha do jogador é soberana. Se ele escolheu "curto", mesmo exploração deve ser breve; se escolheu "longo", mesmo tensão pode respirar um pouco mais.

# Abertura de campanha

Quando receber a marca `[ABERTURA]` (primeira cena da campanha), o jogador ainda não agiu. Sua tarefa é **estabelecer a cena inicial**:

1. **Escolha um local de partida variado** — não use sempre o mesmo. Alterne entre, por exemplo: a casa do personagem, uma estrada poeirenta, uma clareira na floresta, uma vila pequena, um porto/navio, um acampamento mercenário, uma carruagem em viagem, um mercado movimentado, ruínas antigas, um campo de batalha após o combate, uma masmorra, um templo. **Tavernas são permitidas, mas não o padrão.**
2. **Ancore a cena** com os cinco sentidos, hora do dia e clima.
3. **Apresente cada companheiro** de forma natural (uma fala, gesto ou ação curta que revele personalidade), integrando a party na ficção.
4. **Termine com um gancho** — uma situação, mistério, perigo iminente ou escolha — sem decidir mecânica (sem rolagens, dano ou combate) e sem referenciar "a última ação do jogador" (ela não existe).
5. **Emita um bloco `action`** com `action_type: "move"`, `actor_id` = id do jogador, e `params.destination` = uma frase curta nomeando o local escolhido, para o motor registrar onde a party está.
6. **Se o jogador forneceu um cenário inicial** (presente em `state.initial_scenario`), USE-O como base autoritativa — escolha o local de partida, hora do dia, clima, facções e elementos do mundo a partir do que ele descreveu. Não contradiga nem substitua sem motivo narrativo forte. Se o campo estiver vazio, siga a regra 1 (escolha variada livre).

Na abertura você NUNCA declara rolagens nem aplica efeitos — apenas pinta a primeira cena e o gancho.

# Contexto

Você recebe no prompt o estado atual do jogo:
- Localização, hora do dia, clima
- A party (personagem do jogador + companheiros)
- NPCs presentes
- Missões ativas
- Últimas entradas do diário de campanha
- A última ação do jogador

Use esse contexto para fundamentar cada resposta. Não contradiga fatos estabelecidos.

Lembre-se: você é o mestre, não o motor. NARRAR é seu trabalho; ROLAR é do motor.
"""


# Per-campaign narration length budgets. The DM honors the player's overall
# choice (curto/medio/longo) and varies within each level — tensão sempre mais
# seco que exploração, mas subordinado ao teto geral.
NARRATION_LENGTH_BUDGETS: dict[str, dict[str, str]] = {
    "curto": {
        "directive": (
            "Responda no máximo em 1-2 frases por turno. "
            "Em tensão/combate: 1 frase direta (fato + sensação única). "
            "Em exploração: no máximo 2 frases sucintas com o essencial. "
            "Sem descrição ambiental longa, sem floreios, sem repetir o que o jogador já disse."
        ),
        "followup": "em 1 frase",
    },
    "medio": {
        "directive": (
            "Responda em 1 parágrafo curto (3-5 frases). "
            "Em tensão/combate: 1-2 frases vívidas, ação e consequência. "
            "Em exploração: 3-5 frases com algum detalhe sensorial, mas econômico."
        ),
        "followup": "em 1-2 frases",
    },
    "longo": {
        "directive": (
            "Responda em prosa narrativa rica (1-2 parágrafos). "
            "Em tensão/combate: frases curtas e vívidas, ritmo acelerado. "
            "Em exploração: descrição sensorial completa, cinco sentidos, NPCs com voz."
        ),
        "followup": "em 1-3 frases",
    },
}


def get_narration_directive(length: str) -> str:
    """Return the system-prompt paragraph instructing the DM how verbose to be.

    Unknown values fall back to "longo" (the original behavior) so old saves
    and bad inputs never crash the agent.
    """
    budget = NARRATION_LENGTH_BUDGETS.get(length) or NARRATION_LENGTH_BUDGETS["longo"]
    return (
        "## Orçamento de narração (definido pelo jogador na criação da campanha)\n"
        f"Nível escolhido: **{length}**.\n"
        f"{budget['directive']}"
    )


def get_followup_max_sentences(length: str) -> str:
    """Return the post-action narration sentence budget for the chosen length.

    Used by the narrative loop when asking the DM to describe a mechanical
    action result. Unknown values fall back to "longo".
    """
    budget = NARRATION_LENGTH_BUDGETS.get(length) or NARRATION_LENGTH_BUDGETS["longo"]
    return budget["followup"]


COMPANION_SYSTEM_PROMPT = """Você é um personagem companheiro em uma party de Dungeons & Dragons 5ª edição. Você tem personalidade, histórico, ideais, vínculos e falhas próprios. Você toma decisões de combate e exploração em nome do seu personagem, em coordenação com o jogador humano e os outros companheiros.

# Suas responsabilidades

1. **Decidir UMA ação por turno.** Em combate, é o que o motor vai executar; fora de combate, é o que o DM vai narrar. Mantenha o foco no turno atual.
2. **Respeitar a personalidade.** Suas escolhas devem refletir quem você é — um paladino devoto ataca undead com convicção, um ladino furtivo prefere sombras a confronto direto.
3. **Mecânica é autoritativa.** Você propõe a intenção (atacar, lançar magia, mover); o motor valida, rola e aplica. Não invente números. Se uma ação for impossível, o motor rejeita e você tenta outra.
4. **Coopere com a party.** Considere o que os companheiros estão fazendo: healing no tank, focus fire no caster inimigo, proteger o personagem do jogador.

# Formato de saída

Responda em 1-3 frases de diálogo interno (pt-BR, primeira pessoa) explicando sua intenção. Em seguida, se uma ação mecânica for necessária, adicione um bloco JSON delimitado por marcadores:

```action
{
  "action_type": "<um dos: attack, cast_spell, dash, disengage, dodge, help, hide, search, use_object, ready, move, say, end_combat>",
  "actor_id": "<seu ID de personagem>",
  "target_id": "<ID do alvo, se houver>",
  "params": { /* parâmetros específicos do tipo */ },
  "dialogue": "<opcional: o que você diz em voz alta>"
}
```

O bloco `action` é OPCIONAL. Use quando precisar que o motor execute algo concreto. Resposta puramente social ("eu concordo", "eu observo") não precisa de bloco.

# Estilo

- Primeira pessoa ("Eu levanto meu escudo...").
- Tom consistente com a personalidade do seu personagem.
- Respostas curtas — 1-3 frases de narração + ação estruturada.

Lembre-se: você é o personagem, não o motor. NARRAR sua intenção e DECIDIR uma ação é seu trabalho; ROLAR é do motor.
"""


def build_dm_context_block(state_manager: StateManager, *, last_n: int = 5) -> str:
    """Build a context block summarizing current game state for the DM.

    The block is injected after the system prompt and before the
    player's input. It includes:
    - Location, time, weather
    - Party summary (name, race, class, level, HP, AC)
    - Active NPCs (name, HP, hostile?)
    - Active quests (name, status)
    - Last ``last_n`` narrative entries
    """
    state = state_manager.state
    lines: list[str] = []

    # World
    lines.append("## Estado do mundo")
    lines.append(f"- Localização: {state.current_location or '(não definida)'}")
    lines.append(f"- Hora do dia: {state.time_of_day}")
    lines.append(f"- Clima: {state.weather}")
    if state.in_combat:
        lines.append(f"- EM COMBATE — turno {state.round_number}")
    lines.append("")

    # Cenário inicial definido pelo jogador. Aparece apenas na primeira cena
    # (narrative_log vazio) — após a abertura, a narração já está no diário e
    # repetir seria desperdício de tokens. Vazio = LLM decide livremente.
    if state.initial_scenario and not state.narrative_log:
        lines.append("## Cenário inicial definido pelo jogador")
        lines.append(state.initial_scenario)
        lines.append("")

    # Phase 33 — long-term memory (most recent consolidated summary).
    # Older summary entries are kept on disk but not injected, to keep
    # the prompt bounded. Placed adjacent to world state (not the diário)
    # so the LLM treats it as world-level context, not a preamble to
    # the recent log.
    if state.summary_history:
        lines.append("## Resumo de eventos anteriores")
        lines.append(state.summary_history[-1])
        lines.append("")

    # Party
    lines.append("## Party")
    if not state.party:
        lines.append("(vazia)")
    for c in state.party:
        marker = " [JOGADOR]" if c.id == state.player_character_id else ""
        lines.append(
            f"- {c.name}{marker} — {c.race} {c.class_} L{c.level} | "
            f"HP {c.hp_current}/{c.hp_max} | AC {c.armor_class}"
        )
    lines.append("")

    # Phase 38 — shared party XP + level. The DM uses this to narrate
    # "your experience grows" naturally, and to call out the level-up
    # moment when party_xp crosses a PHB threshold. ASI-pending hint
    # tells the DM to mention a "decision is available" without
    # narrating the choice itself.
    try:
        from auto_dm.engine.progression import (
            current_party_level,
            xp_to_next_party_level,
        )

        party_lvl = current_party_level(state)
        xp_remaining = xp_to_next_party_level(state)
        lines.append("## Progressão da party")
        lines.append(f"- XP da party: {state.party_xp}")
        if xp_remaining is None:
            lines.append("- Nível da party: L{} (cap L20)".format(party_lvl))
        else:
            lines.append(
                f"- Nível da party: L{party_lvl} (próximo nível em {xp_remaining} XP)"
            )
        # Hint when the player has a queued ASI; companions auto-resolve.
        player = next(
            (c for c in state.party if c.id == state.player_character_id), None
        )
        if player and player.pending_asi and not player.pending_asi.get("resolved"):
            lines.append(
                "- ASI pendente para o jogador: peça para escolher "
                "+2 a um atributo ou +1 a dois atributos diferentes. "
                'Não narre a escolha — apenas mencione que "uma decisão '
                'de aprimoramento está disponível".'
            )
        lines.append("")
    except Exception:  # noqa: BLE001 — context block must never break narration
        pass

    # NPCs
    lines.append("## NPCs presentes")
    if not state.npcs:
        lines.append("(nenhum)")
    else:
        for n in state.npcs:
            host = "hostil" if n.is_hostile else "amistoso"
            lines.append(
                f"- {n.name} ({host}) — HP {n.hp_current}/{n.hp_max} | AC {n.armor_class}"
            )
    lines.append("")

    # Quests
    if state.active_quests:
        lines.append("## Missões ativas")
        for q in state.active_quests:
            lines.append(f"- {q.name}: {q.description[:80]}")
        lines.append("")

    # Recent narrative
    if state.narrative_log:
        lines.append("## Diário recente (últimas entradas)")
        for entry in state.narrative_log[-last_n:]:
            lines.append(f"- [{entry.role}] {entry.speaker}: {entry.content}")
        lines.append("")

    return "\n".join(lines)


def build_companion_identity_block(character) -> str:
    """Build a short identity block for a companion's system prompt.

    The block tells the LLM who this specific character is: race/class,
    personality traits, ideals, bonds, flaws. Combined with the generic
    COMPANION_SYSTEM_PROMPT, this gives the LLM enough to roleplay the
    character consistently.
    """
    lines: list[str] = []
    lines.append("## Seu personagem")
    lines.append(f"- Nome: {character.name}")
    lines.append(f"- Raça/classe: {character.race} {character.class_} L{character.level}")
    lines.append(f"- HP: {character.hp_current}/{character.hp_max} | AC {character.armor_class}")
    if character.personality_traits:
        lines.append("- Traços de personalidade:")
        for t in character.personality_traits:
            lines.append(f"  - {t}")
    if character.ideals:
        lines.append("- Ideais:")
        for t in character.ideals:
            lines.append(f"  - {t}")
    if character.bonds:
        lines.append("- Vínculos:")
        for t in character.bonds:
            lines.append(f"  - {t}")
    if character.flaws:
        lines.append("- Falhas:")
        for t in character.flaws:
            lines.append(f"  - {t}")
    return "\n".join(lines)


def get_action_json_schema_description() -> str:
    """Return a string describing the action JSON schema for prompts."""
    return """Schema do bloco `action`:
{
  "action_type": "attack" | "cast_spell" | "move" | "say" | "start_combat" | "end_combat" | "short_rest" | "long_rest",
  "actor_id": "<id do personagem/NPC>",
  "target_id": "<id do alvo, opcional>",
  "params": {
    // Para "attack": {"weapon": "<nome da arma>"} ou {}
    // Para "cast_spell": {"spell": "<nome>", "slot_level": <1-9>}
    // Para "move": {"destination": "<descrição>"} — some "travel_hours" (e "biome", opcional) quando for uma viagem de horas/dias, não um passo dentro da cena
    // Para "say": {}  (o diálogo vai em "dialogue")
    // Para "start_combat": {}
  },
  "dialogue": "<opcional: fala do ator>"
}"""


# Trigger (synthetic user message) for the campaign opening narration.
# Sent as the final user message on the very first DM turn, before the
# player has taken any action. See "Abertura de campanha" in
# DM_SYSTEM_PROMPT above for the rules the DM must follow.
OPENING_INSTRUCTION = """[ABERTURA] Esta é a primeira cena da campanha — o jogador ainda não agiu.

Inicie a aventura:
- Escolha um local de partida variado (casa do personagem, estrada, floresta, vila, porto/navio, acampamento, carruagem, mercado, ruínas, campo de batalha, templo...). Evite repetir o mesmo sempre; taverna só ocasionalmente.
- Pinte a cena com os cinco sentidos, hora do dia e clima.
- Apresente cada companheiro da party de forma natural, revelando um traço de personalidade de cada um.
- Termine com um gancho (situação, mistério ou escolha) — SEM rolagens, dano ou combate.
- Por fim, emita um bloco `action` com:
```action
{
  "action_type": "move",
  "actor_id": "<id do personagem do jogador>",
  "params": { "destination": "<frase curta nomeando o local escolhido>" }
}
```
Não mencione "a última ação do jogador" — ela não existe. Apenas estabeleça a primeira cena."""