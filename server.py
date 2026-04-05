"""
Sport Match Agent - Boston Sports Schedule + Match Prediction
Fetches upcoming games and predicts outcomes using Claude AI.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import logging

import cache
import scheduler
from predictor import predict_match as run_prediction, fetch_upcoming_games, TEAMS as PREDICTOR_TEAMS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# LIFESPAN — start/stop heartbeat scheduler
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start(run_immediately=True)
    yield
    scheduler.stop()


app = FastAPI(title="Sport Match Agent", version="2.0.0", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TEAMS = PREDICTOR_TEAMS


class MCPDecideRequest(BaseModel):
    """MCP /decide request format"""
    agent_id: str
    state: Dict[str, Any]


class MCPAction(BaseModel):
    """MCP action format"""
    action: str
    target: Optional[str] = None
    amount: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None


# ============================================================================
# INTERNAL HELPERS — cache-first schedule fetching
# ============================================================================

async def get_team_games(team_key: str, days: int = 14) -> List[Dict[str, Any]]:
    """Return schedule for a team — from cache if fresh, else live from ESPN."""
    cached = cache.get(team_key)
    if cached is not None:
        logger.info(f"📦 Serving '{team_key}' from cache")
        return cached

    logger.info(f"🌐 Cache miss for '{team_key}' — fetching live")
    games = await fetch_upcoming_games(team_key, days_ahead=days)
    cache.set(team_key, games)
    return games


async def get_all_games(days: int = 14) -> List[Dict[str, Any]]:
    """Return all upcoming games — from cache if fresh, else live."""
    cached = cache.get("all")
    if cached is not None:
        logger.info("📦 Serving 'all' from cache")
        return cached

    all_games = []
    for team_key in TEAMS:
        games = await get_team_games(team_key, days)
        all_games.extend(games)
    all_games.sort(key=lambda g: g["date"])
    cache.set("all", all_games)
    return all_games


# ============================================================================
# MCP PROTOCOL ENDPOINTS
# ============================================================================

@app.post("/decide")
async def mcp_decide(request: MCPDecideRequest) -> Dict[str, Any]:
    """
    MCP /decide endpoint - called by AgenticTown orchestrator each cycle.

    Supports two modes via state:
    - Default: returns upcoming schedule summary (served from cache)
    - Prediction mode: if state contains {"predict": {"team": "<key>", "game_index": 0}},
      returns a Claude-powered match prediction for that game
    """
    cycle = request.state.get("cycle", 0)
    logger.info(f"⚽ Sport Match Agent decision cycle {cycle}")

    # Check if the requesting agent wants a prediction
    predict_request = request.state.get("predict")
    if predict_request and isinstance(predict_request, dict):
        team_key = predict_request.get("team", "").lower()
        game_index = int(predict_request.get("game_index", 0))

        if team_key in TEAMS:
            logger.info(f"🔮 Prediction requested for {team_key} game #{game_index}")
            result = await run_prediction(team_key, game_index)

            if "error" not in result:
                pred = result["prediction"]
                game = result["game"]
                message = (
                    f"🔮 Prediction: {game['away']} @ {game['home']} ({game['date']})\n"
                    f"Winner: {pred['predicted_winner']} "
                    f"({pred['home_win_probability']}% home / {pred['away_win_probability']}% away)\n"
                    f"Confidence: {pred['confidence']} | {pred['analysis']}"
                )
                return {
                    "agent_id": request.agent_id,
                    "actions": [],
                    "message": message,
                    "metadata": {
                        "type": "prediction",
                        "prediction": result,
                        "last_updated": datetime.now(timezone.utc).isoformat(),
                    },
                }

    # Default: return schedule summary from cache
    games = await get_all_games(days=7)
    cache_meta = cache.get_meta("all")

    if not games:
        message = "📅 No upcoming Boston sports games in the next 7 days"
    else:
        lines = [f"📅 Upcoming Boston Sports ({len(games)} games):"]
        for game in games[:5]:
            lines.append(
                f"{game['emoji']} {game['sport']}: "
                f"{game['away']} @ {game['home']} - {game['date_str']}"
            )
        message = "\n".join(lines)

    return {
        "agent_id": request.agent_id,
        "actions": [],
        "message": message,
        "metadata": {
            "type": "schedule",
            "games_count": len(games),
            "games": games[:10],
            "cache": cache_meta,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "hint": "To request a prediction, include {\"predict\": {\"team\": \"celtics\", \"game_index\": 0}} in state",
        },
    }


# ============================================================================
# STANDALONE ENDPOINTS
# ============================================================================

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "app": "Sport Match Agent",
        "version": "2.0.0",
        "description": "Boston sports schedule + AI match prediction (Patriots, Celtics, Bruins, Red Sox)",
        "teams": list(TEAMS.keys()),
        "endpoints": {
            "/schedule": "Get all upcoming games (cache-first)",
            "/schedule/{team}": "Get games for specific team (cache-first)",
            "/predict/{team}": "Predict next game outcome for a team (AI-powered, on-demand)",
            "/predict/{team}/{game_index}": "Predict a specific upcoming game by index",
            "/heartbeat/status": "View heartbeat scheduler status and cache info",
            "/decide": "MCP protocol endpoint for AgenticTown",
        },
    }


@app.get("/schedule")
async def get_schedule(days: int = 14):
    """Get all upcoming Boston sports games (served from cache when fresh)."""
    games = await get_all_games(days=days)
    meta = cache.get_meta("all")
    return {
        "success": True,
        "count": len(games),
        "cache": meta,
        "games": games,
    }


@app.get("/schedule/{team}")
async def get_team_schedule(team: str, days: int = 14):
    """Get upcoming games for a specific team (served from cache when fresh)."""
    team = team.lower()
    if team not in TEAMS:
        return {
            "success": False,
            "error": f"Unknown team: {team}",
            "available": list(TEAMS.keys()),
        }

    games = await get_team_games(team, days=days)
    meta = cache.get_meta(team)
    return {
        "success": True,
        "team": TEAMS[team]["name"],
        "sport": TEAMS[team]["sport"],
        "count": len(games),
        "cache": meta,
        "games": games,
    }


@app.get("/heartbeat/status")
async def heartbeat_status():
    """Show heartbeat scheduler status and current cache state."""
    jobs = scheduler.scheduler.get_jobs()
    job_info = []
    for job in jobs:
        job_info.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        })

    cache_state = {}
    for key in list(TEAMS.keys()) + ["all"]:
        meta = cache.get_meta(key)
        cache_state[key] = meta if meta else "empty"

    return {
        "scheduler_running": scheduler.scheduler.running,
        "jobs": job_info,
        "cache": cache_state,
    }


@app.get("/health")
async def health():
    """Health check"""
    return {
        "status": "healthy",
        "app": "Sport Match Agent",
        "version": "2.0.0",
        "scheduler_running": scheduler.scheduler.running,
    }


# ============================================================================
# PREDICTION ENDPOINTS
# ============================================================================

@app.get("/predict/{team}")
async def predict_next_game(team: str):
    """Predict the outcome of the next upcoming game for a team (on-demand)."""
    team = team.lower()
    if team not in TEAMS:
        return {
            "success": False,
            "error": f"Unknown team: {team}",
            "available": list(TEAMS.keys()),
        }

    result = await run_prediction(team, game_index=0)
    if "error" in result:
        return {"success": False, "error": result["error"]}
    return {"success": True, **result}


@app.get("/predict/{team}/{game_index}")
async def predict_game_by_index(team: str, game_index: int):
    """Predict a specific upcoming game by index for a team (on-demand)."""
    team = team.lower()
    if team not in TEAMS:
        return {
            "success": False,
            "error": f"Unknown team: {team}",
            "available": list(TEAMS.keys()),
        }

    result = await run_prediction(team, game_index=game_index)
    if "error" in result:
        return {"success": False, "error": result["error"]}
    return {"success": True, **result}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
