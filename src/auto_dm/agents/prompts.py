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

# Estilo

- Frases curtas e vívidas em momentos de tensão; descrições longas em exploração.
- Use os cinco sentidos (visão, som, cheiro, tato, paladar) para ancorar a cena.
- Não force conclusões — descreva o que o jogador percebe e oferece opções.
- NPCs têm voz própria; personalidades distintas; motive cada um.

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
    // Para "move": {"destination": "<descrição>"}
    // Para "say": {}  (o diálogo vai em "dialogue")
    // Para "start_combat": {}
  },
  "dialogue": "<opcional: fala do ator>"
}"""