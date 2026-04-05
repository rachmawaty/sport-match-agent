"""
Match Prediction Engine
Fetches team stats, recent form, and head-to-head history from ESPN,
then uses Claude to reason over the data and produce a structured prediction.
"""

import httpx
import logging
import json
from datetime import datetime, timezone, timedelta
from dateutil import parser as dateparser
from typing import Optional
import anthropic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# ESPN API CONFIG
# ============================================================================

TEAMS = {
    "patriots": {
        "name": "New England Patriots",
        "sport": "NFL",
        "league": "nfl",
        "sport_path": "football",
        "espn_id": "ne",
        "emoji": "🏈",
    },
    "celtics": {
        "name": "Boston Celtics",
        "sport": "NBA",
        "league": "nba",
        "sport_path": "basketball",
        "espn_id": "bos",
        "emoji": "🏀",
    },
    "bruins": {
        "name": "Boston Bruins",
        "sport": "NHL",
        "league": "nhl",
        "sport_path": "hockey",
        "espn_id": "bos",
        "emoji": "🏒",
    },
    "redsox": {
        "name": "Boston Red Sox",
        "sport": "MLB",
        "league": "mlb",
        "sport_path": "baseball",
        "espn_id": "bos",
        "emoji": "⚾",
    },
}

ESPN_BASE = "http://site.api.espn.com/apis/site/v2/sports"


# ============================================================================
# DATA FETCHERS
# ============================================================================

async def fetch_upcoming_games(team_key: str, days_ahead: int = 14) -> list[dict]:
    """Fetch upcoming scheduled games for a team."""
    team = TEAMS[team_key]
    url = f"{ESPN_BASE}/{team['sport_path']}/{team['league']}/teams/{team['espn_id']}/schedule"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=days_ahead)
        games = []

        for event in data.get("events", []):
            try:
                game_date_str = event.get("date")
                if not game_date_str:
                    continue
                game_date = dateparser.parse(game_date_str)
                if game_date < now or game_date > cutoff:
                    continue

                comp = (event.get("competitions") or [{}])[0]
                home_team, away_team = None, None
                home_id, away_id = None, None

                for competitor in comp.get("competitors", []):
                    t = competitor.get("team", {})
                    name = t.get("displayName", "Unknown")
                    tid = t.get("id")
                    if competitor.get("homeAway") == "home":
                        home_team, home_id = name, tid
                    else:
                        away_team, away_id = name, tid

                games.append({
                    "event_id": event.get("id"),
                    "team": team["name"],
                    "sport": team["sport"],
                    "league": team["league"],
                    "sport_path": team["sport_path"],
                    "emoji": team["emoji"],
                    "date": game_date.isoformat(),
                    "date_str": game_date.strftime("%a, %b %d at %I:%M %p UTC"),
                    "home": home_team or "TBD",
                    "away": away_team or "TBD",
                    "home_id": home_id,
                    "away_id": away_id,
                    "venue": comp.get("venue", {}).get("fullName", "TBD"),
                    "status": event.get("status", {}).get("type", {}).get("name", "Scheduled"),
                })
            except Exception as e:
                logger.warning(f"Error parsing game for {team_key}: {e}")

        return games

    except Exception as e:
        logger.error(f"Failed to fetch schedule for {team_key}: {e}")
        return []


async def fetch_team_record(sport_path: str, league: str, team_id: str) -> dict:
    """Fetch current season win/loss record for a team."""
    url = f"{ESPN_BASE}/{sport_path}/{league}/teams/{team_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

        team_data = data.get("team", {})
        record_summary = team_data.get("record", {}).get("items", [])

        records = {}
        for item in record_summary:
            label = item.get("type", "overall")
            records[label] = item.get("summary", "N/A")

        return {
            "name": team_data.get("displayName", "Unknown"),
            "record": records,
            "standing": team_data.get("standingSummary", "N/A"),
        }

    except Exception as e:
        logger.warning(f"Failed to fetch record for team {team_id}: {e}")
        return {}


async def fetch_recent_form(sport_path: str, league: str, team_espn_id: str, last_n: int = 5) -> list[dict]:
    """Fetch last N completed games for a team to assess recent form."""
    url = f"{ESPN_BASE}/{sport_path}/{league}/teams/{team_espn_id}/schedule"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

        now = datetime.now(timezone.utc)
        completed = []

        for event in data.get("events", []):
            try:
                status_name = event.get("status", {}).get("type", {}).get("name", "")
                if status_name not in ("STATUS_FINAL", "Final", "STATUS_FINAL_OVERTIME"):
                    continue

                game_date_str = event.get("date")
                if not game_date_str:
                    continue
                game_date = dateparser.parse(game_date_str)
                if game_date > now:
                    continue

                comp = (event.get("competitions") or [{}])[0]
                home_team, away_team = None, None
                home_score, away_score = None, None
                winner = None

                for competitor in comp.get("competitors", []):
                    t = competitor.get("team", {})
                    name = t.get("displayName", "Unknown")
                    score = competitor.get("score", "0")
                    is_winner = competitor.get("winner", False)
                    if competitor.get("homeAway") == "home":
                        home_team, home_score = name, score
                        if is_winner:
                            winner = name
                    else:
                        away_team, away_score = name, score
                        if is_winner:
                            winner = name

                completed.append({
                    "date": game_date.strftime("%b %d"),
                    "home": home_team,
                    "away": away_team,
                    "home_score": home_score,
                    "away_score": away_score,
                    "winner": winner,
                })
            except Exception as e:
                logger.warning(f"Error parsing past game: {e}")

        # Return most recent N games
        completed.sort(key=lambda g: g["date"], reverse=True)
        return completed[:last_n]

    except Exception as e:
        logger.warning(f"Failed to fetch recent form: {e}")
        return []


async def fetch_head_to_head(sport_path: str, league: str, team1_espn_id: str, team2_name: str, last_n: int = 5) -> list[dict]:
    """Fetch recent head-to-head results between two teams."""
    url = f"{ESPN_BASE}/{sport_path}/{league}/teams/{team1_espn_id}/schedule"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

        now = datetime.now(timezone.utc)
        h2h = []

        for event in data.get("events", []):
            try:
                status_name = event.get("status", {}).get("type", {}).get("name", "")
                if status_name not in ("STATUS_FINAL", "Final", "STATUS_FINAL_OVERTIME"):
                    continue

                game_date_str = event.get("date")
                if not game_date_str:
                    continue
                game_date = dateparser.parse(game_date_str)
                if game_date > now:
                    continue

                comp = (event.get("competitions") or [{}])[0]
                competitors = comp.get("competitors", [])
                team_names = [c.get("team", {}).get("displayName", "") for c in competitors]

                # Check if this is a matchup against team2
                if not any(team2_name.lower() in name.lower() for name in team_names):
                    continue

                home_team, away_team = None, None
                home_score, away_score = None, None
                winner = None

                for competitor in competitors:
                    t = competitor.get("team", {})
                    name = t.get("displayName", "Unknown")
                    score = competitor.get("score", "0")
                    is_winner = competitor.get("winner", False)
                    if competitor.get("homeAway") == "home":
                        home_team, home_score = name, score
                        if is_winner:
                            winner = name
                    else:
                        away_team, away_score = name, score
                        if is_winner:
                            winner = name

                h2h.append({
                    "date": game_date.strftime("%b %d, %Y"),
                    "home": home_team,
                    "away": away_team,
                    "home_score": home_score,
                    "away_score": away_score,
                    "winner": winner,
                })
            except Exception as e:
                logger.warning(f"Error parsing H2H game: {e}")

        h2h.sort(key=lambda g: g["date"], reverse=True)
        return h2h[:last_n]

    except Exception as e:
        logger.warning(f"Failed to fetch H2H: {e}")
        return []


# ============================================================================
# CLAUDE PREDICTION ENGINE
# ============================================================================

async def predict_match(team_key: str, game_index: int = 0) -> dict:
    """
    Full prediction pipeline for an upcoming game:
    1. Fetch upcoming games
    2. Gather stats, recent form, and H2H for both teams
    3. Ask Claude to reason over the data and produce a prediction
    """
    if team_key not in TEAMS:
        return {"error": f"Unknown team '{team_key}'. Valid: {', '.join(TEAMS.keys())}"}

    team = TEAMS[team_key]

    # Step 1: Get upcoming games
    games = await fetch_upcoming_games(team_key, days_ahead=30)
    if not games:
        return {"error": f"No upcoming games found for {team['name']}"}
    if game_index >= len(games):
        return {"error": f"Game index {game_index} out of range. Only {len(games)} upcoming games."}

    game = games[game_index]
    sport_path = team["sport_path"]
    league = team["league"]
    home_name = game["home"]
    away_name = game["away"]

    logger.info(f"Predicting: {away_name} @ {home_name} ({game['date_str']})")

    # Step 2: Gather data in parallel using asyncio
    import asyncio

    home_record_task = fetch_team_record(sport_path, league, game["home_id"]) if game.get("home_id") else asyncio.sleep(0, result={})
    away_record_task = fetch_team_record(sport_path, league, game["away_id"]) if game.get("away_id") else asyncio.sleep(0, result={})
    home_form_task = fetch_recent_form(sport_path, league, team["espn_id"] if home_name == team["name"] else game.get("home_id", team["espn_id"]))
    away_form_task = fetch_recent_form(sport_path, league, game.get("away_id", team["espn_id"]))
    h2h_task = fetch_head_to_head(sport_path, league, team["espn_id"], away_name if home_name == team["name"] else home_name)

    home_record, away_record, home_form, away_form, h2h = await asyncio.gather(
        home_record_task,
        away_record_task,
        home_form_task,
        away_form_task,
        h2h_task,
    )

    # Step 3: Build context for Claude
    context = {
        "game": {
            "home_team": home_name,
            "away_team": away_name,
            "date": game["date_str"],
            "venue": game["venue"],
            "sport": game["sport"],
        },
        "home_team_record": home_record,
        "away_team_record": away_record,
        "home_team_recent_form": home_form,
        "away_team_recent_form": away_form,
        "head_to_head_history": h2h,
    }

    # Step 4: Call Claude
    client = anthropic.Anthropic()

    system_prompt = """You are an expert sports analyst. You analyze team statistics, recent form, and head-to-head history to predict match outcomes.
You must respond ONLY with a valid JSON object — no markdown, no explanation outside the JSON.

The JSON must have exactly these fields:
{
  "predicted_winner": "<team name>",
  "home_win_probability": <integer 0-100>,
  "away_win_probability": <integer 0-100>,
  "confidence": "<low|medium|high>",
  "key_factors": ["<factor 1>", "<factor 2>", "<factor 3>"],
  "analysis": "<2-4 sentence explanation of the prediction reasoning>"
}

home_win_probability + away_win_probability must equal 100."""

    user_prompt = f"""Predict the outcome of this upcoming {game['sport']} game based on the following data:

{json.dumps(context, indent=2)}

Analyze all available data — season records, recent form (last 5 games), and head-to-head history — to make your prediction."""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": user_prompt}],
            system=system_prompt,
        )

        raw = message.content[0].text.strip()
        prediction = json.loads(raw)

        return {
            "game": {
                "home": home_name,
                "away": away_name,
                "date": game["date_str"],
                "venue": game["venue"],
                "sport": game["sport"],
                "emoji": game["emoji"],
            },
            "prediction": prediction,
            "data_used": {
                "home_record": home_record.get("record", {}),
                "away_record": away_record.get("record", {}),
                "home_recent_form": [f"{g['away']} @ {g['home']}: {g['away_score']}-{g['home_score']} (W: {g['winner']})" for g in home_form],
                "away_recent_form": [f"{g['away']} @ {g['home']}: {g['away_score']}-{g['home_score']} (W: {g['winner']})" for g in away_form],
                "head_to_head": [f"{g['away']} @ {g['home']}: {g['away_score']}-{g['home_score']} (W: {g['winner']})" for g in h2h],
            },
        }

    except json.JSONDecodeError as e:
        logger.error(f"Claude returned invalid JSON: {e}")
        return {"error": "Prediction engine returned malformed response", "raw": raw}
    except Exception as e:
        logger.error(f"Claude prediction failed: {e}")
        return {"error": str(e)}
