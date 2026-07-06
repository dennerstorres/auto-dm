# World tables

Tabelas de encontros aleatórios, tesouro e clima usadas por `engine/world.py`
(Fase 40). Este é um recurso **original/curado**, não conteúdo do PHB —
por isso vive fora de `data/phb/` e usa um loader e um cache separados
(`phb/lookup.py::get_world_tables_root` / `set_world_tables_root`).

## Formato

### `encounters/*.json`

```json
{
  "id": "forest_day",
  "name": "Floresta — Dia",
  "biome": "forest",
  "time_of_day": "day",
  "entries": [
    { "roll_min": 1, "roll_max": 60, "monsters": [], "notes": "Nenhum encontro." },
    { "roll_min": 61, "roll_max": 75, "monsters": [{ "id": "Wolf", "count": "2d4" }], "notes": "..." }
  ]
}
```

Rolagem é sempre **d100**. `monsters[].id` deve bater exatamente com o
`name` de um `Monster` carregado por `auto_dm.phb.get_monster` (ver
`data/phb/Monsters/`). `count` é notação de dado (`"2d4"`) ou um número
fixo (`"1"`).

`engine/world.py::resolve_travel` busca a tabela por
`f"{biome}_{time_of_day}"` (`time_of_day` é `"day"` ou `"night"`,
derivado do relógio de jogo). As tabelas `dungeon_level_*` não seguem
essa convenção — ficam disponíveis para uso futuro em masmorras, fora
do fluxo padrão de viagem.

### `loot/*.json`

```json
{
  "id": "hoard_low",
  "name": "Tesouro — Nível baixo (CR 0-4)",
  "tier": "low",
  "entries": [
    { "roll_min": 1, "roll_max": 20, "gold_dice": "4d6", "gold_multiplier": 100.0, "items": [], "notes": "..." }
  ]
}
```

Rolagem também é **d100**. Ouro final = `roll_dice(gold_dice).total *
gold_multiplier` (ambos os campos são opcionais — linha sem
`gold_dice` não dá ouro). `items` são nomes de catálogo resolvidos via
`engine/inventory.py::resolve_catalog_item` (armas, armaduras,
equipamento geral ou itens mágicos do PHB).

### `weather.json`

Rolagem **d20**, tabela única (sem variação por bioma/estação — ver
nota de escopo abaixo).

## Simplificações conscientes desta fase (vs. o DMG completo)

- Tabelas com ~5-6 entradas cobrindo o d100 inteiro, não as ~100 linhas
  do DMG — curadoria manual, focada em conteúdo de baixo nível (L1-5).
- Clima é uma tabela genérica única, não uma por bioma/estação.
- Tesouro de masmorra ("hoard") não está condicionado a derrotar o
  encontro — é um evento de viagem independente (achado diário,
  tabela `individual`), já que a Fase 40 não amarra loot ao resultado
  do combate. Tabelas `hoard_*` ficam carregadas e testáveis via
  `compute_loot()` para uso futuro (ex.: recompensa pós-combate).
