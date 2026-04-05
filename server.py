"""
Sport Match Agent - Boston Sports Schedule + Match Prediction
Fetches upcoming games and predicts outcomes using Claude AI.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import httpx
from datetime import datetime, timedelta
from dateutil import parser
import logging
from predictor import predict_match as run_prediction, fetch_upcoming_games, TEAMS as PREDICTOR_TEAMS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Sport Match Agent", version="2.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Team configurations - ESPN API endpoints
TEAMS = {
    "patriots": {
        "name": "New England Patriots",
        "sport": "NFL",
        "emoji": "🏈",
        "api": "http://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/ne/schedule"
    },
    "celtics": {
        "name": "Boston Celtics",
        "sport": "NBA",
        "emoji": "🏀",
        "api": "http://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/bos/schedule"
    },
    "bruins": {
        "name": "Boston Bruins",
        "sport": "NHL",
        "emoji": "🏒",
        "api": "http://site.api.espn.com/apis/site/v2/sports/hockey/nhl/teams/bos/schedule"
    },
    "redsox": {
        "name": "Boston Red Sox",
        "sport": "MLB",
        "emoji": "⚾",
        "api": "http://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/bos/schedule"
    }
}


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


async def fetch_team_schedule(team_key: str, days_ahead: int = 14) -> List[Dict[str, Any]]:
    """Fetch upcoming games for a team from ESPN API"""
    team = TEAMS[team_key]
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(team["api"])
            response.raise_for_status()
            data = response.json()
        
        games = []
        from datetime import timezone
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=days_ahead)
        
        # Parse ESPN response
        events = data.get("events", [])
        
        for event in events:
            try:
                game_date_str = event.get("date")
                if not game_date_str:
                    continue
                
                game_date = parser.parse(game_date_str)
                
                # Only include upcoming games within the window
                if game_date < now or game_date > cutoff:
                    continue
                
                competitions = event.get("competitions", [])
                if not competitions:
                    continue
                
                comp = competitions[0]
                competitors = comp.get("competitors", [])
                
                home_team = None
                away_team = None
                
                for competitor in competitors:
                    team_info = competitor.get("team", {})
                    if competitor.get("homeAway") == "home":
                        home_team = team_info.get("displayName", "Unknown")
                    else:
                        away_team = team_info.get("displayName", "Unknown")
                
                venue = comp.get("venue", {}).get("fullName", "TBD")
                status = event.get("status", {}).get("type", {}).get("name", "Scheduled")
                
                game = {
                    "team": team["name"],
                    "sport": team["sport"],
                    "emoji": team["emoji"],
                    "date": game_date.isoformat(),
                    "date_str": game_date.strftime("%a, %b %d at %I:%M %p"),
                    "home": home_team,
                    "away": away_team,
                    "venue": venue,
                    "status": status
                }
                
                games.append(game)
                
            except Exception as e:
                logger.warning(f"Error parsing game for {team_key}: {e}")
                continue
        
        return games
        
    except Exception as e:
        logger.error(f"Failed to fetch schedule for {team_key}: {e}")
        return []


async def get_all_upcoming_games(days_ahead: int = 14) -> List[Dict[str, Any]]:
    """Get all upcoming games for all Boston teams"""
    all_games = []
    
    for team_key in TEAMS.keys():
        games = await fetch_team_schedule(team_key, days_ahead)
        all_games.extend(games)
    
    # Sort by date
    all_games.sort(key=lambda g: g["date"])
    
    return all_games


# ============================================================================
# MCP PROTOCOL ENDPOINTS
# ============================================================================

@app.post("/decide")
async def mcp_decide(request: MCPDecideRequest) -> Dict[str, Any]:
    """
    MCP /decide endpoint - called by AgenticTown orchestrator each cycle.

    Supports two modes via state:
    - Default: returns upcoming schedule summary
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

        if team_key in PREDICTOR_TEAMS:
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
                        "last_updated": datetime.now().isoformat(),
                    },
                }

    # Default: return schedule summary
    games = await get_all_upcoming_games(days_ahead=7)

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
            "last_updated": datetime.now().isoformat(),
            "hint": "To request a prediction, include {\"predict\": {\"team\": \"celtics\", \"game_index\": 0}} in state",
        },
    }


# ============================================================================
# STANDALONE ENDPOINTS (for direct querying)
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
            "/schedule": "Get all upcoming games",
            "/schedule/{team}": "Get games for specific team",
            "/predict/{team}": "Predict next game outcome for a team (AI-powered)",
            "/predict/{team}/{game_index}": "Predict a specific upcoming game by index",
            "/decide": "MCP protocol endpoint for AgenticTown (supports prediction requests via state)",
        }
    }


@app.get("/schedule")
async def get_schedule(days: int = 14):
    """Get all upcoming Boston sports games"""
    games = await get_all_upcoming_games(days_ahead=days)
    
    return {
        "success": True,
        "count": len(games),
        "games": games
    }


@app.get("/schedule/{team}")
async def get_team_schedule(team: str, days: int = 14):
    """Get upcoming games for specific team"""
    team = team.lower()
    
    if team not in TEAMS:
        return {
            "success": False,
            "error": f"Unknown team: {team}",
            "available": list(TEAMS.keys())
        }
    
    games = await fetch_team_schedule(team, days_ahead=days)
    
    return {
        "success": True,
        "team": TEAMS[team]["name"],
        "sport": TEAMS[team]["sport"],
        "count": len(games),
        "games": games
    }


@app.get("/health")
async def health():
    """Health check"""
    return {"status": "healthy", "app": "Sport Match Agent", "version": "2.0.0"}


# ============================================================================
# PREDICTION ENDPOINTS
# ============================================================================

@app.get("/predict/{team}")
async def predict_next_game(team: str):
    """Predict the outcome of the next upcoming game for a team."""
    team = team.lower()
    if team not in PREDICTOR_TEAMS:
        return {
            "success": False,
            "error": f"Unknown team: {team}",
            "available": list(PREDICTOR_TEAMS.keys())
        }

    result = await run_prediction(team, game_index=0)

    if "error" in result:
        return {"success": False, "error": result["error"]}

    return {"success": True, **result}


@app.get("/predict/{team}/{game_index}")
async def predict_game_by_index(team: str, game_index: int):
    """Predict the outcome of a specific upcoming game (by index) for a team."""
    team = team.lower()
    if team not in PREDICTOR_TEAMS:
        return {
            "success": False,
            "error": f"Unknown team: {team}",
            "available": list(PREDICTOR_TEAMS.keys())
        }

    result = await run_prediction(team, game_index=game_index)

    if "error" in result:
        return {"success": False, "error": result["error"]}

    return {"success": True, **result}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
