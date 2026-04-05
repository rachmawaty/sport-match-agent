# Skills.md — Sport Match Agent

This file defines the skills (tools) available in the Sport Match Agent.
Other agents or humans can invoke these skills directly via MCP or REST API.

---

## Skill: `get_schedule`

**Description:** Fetch upcoming game schedules for Boston sports teams.

**Trigger phrases:**
- "What games are coming up?"
- "Show me the schedule for [team]"
- "When do the [team] play next?"

**Parameters:**
| Parameter | Type   | Default | Description                                              |
|-----------|--------|---------|----------------------------------------------------------|
| `team`    | string | `"all"` | Team key: `patriots`, `celtics`, `bruins`, `redsox`, or `all` |
| `days`    | int    | `14`    | Number of days ahead to look (1–30)                      |

**MCP tool:** `get_boston_sports_schedule(team, days)`
**REST:** `GET /schedule` or `GET /schedule/{team}?days=14`

---

## Skill: `list_teams`

**Description:** List all supported Boston sports teams and their keys.

**Trigger phrases:**
- "What teams do you support?"
- "List available teams"

**Parameters:** None

**MCP tool:** `list_supported_teams()`
**REST:** `GET /`

---

## Skill: `predict_match`

**Description:** Predict the outcome of an upcoming game using AI analysis.
Analyzes team records, recent form (last 5 games), and head-to-head history,
then uses Claude to produce win probabilities, key factors, and a reasoning explanation.

> ⚠️ This skill is **on-demand only** — it will not run automatically.
> It must be explicitly requested by a human or another agent.

**Trigger phrases:**
- "Predict the next [team] game"
- "Who will win [team] vs [opponent]?"
- "Give me a match prediction for [team]"

**Parameters:**
| Parameter    | Type   | Default | Description                                      |
|--------------|--------|---------|--------------------------------------------------|
| `team`       | string | —       | Team key: `patriots`, `celtics`, `bruins`, `redsox` |
| `game_index` | int    | `0`     | Which upcoming game (0 = next, 1 = after that, …) |

**MCP tool:** `predict_match(team, game_index)`
**REST:** `GET /predict/{team}` or `GET /predict/{team}/{game_index}`
**Agent-to-agent:** POST `/decide` with `state: { "predict": { "team": "celtics", "game_index": 0 } }`

**Output includes:**
- Predicted winner
- Home / away win probability (%)
- Confidence level (low / medium / high)
- Key factors influencing the prediction
- Short analysis paragraph
