"""
Heartbeat Scheduler
Implements the 24h heartbeat defined in Heartbeat.md.

Runs daily at 06:00 UTC:
- Fetches upcoming schedules for all Boston teams from ESPN
- Writes results to the schedule cache
- Does NOT generate predictions (those are on-demand only)
"""

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import cache
from predictor import fetch_upcoming_games, TEAMS

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


async def refresh_schedule_cache() -> None:
    """
    Heartbeat job: fetch schedules for all teams and update the cache.
    Runs daily at 06:00 UTC as defined in Heartbeat.md.
    No predictions are generated here.
    """
    logger.info("💓 Heartbeat fired — refreshing schedule cache")
    started_at = datetime.now(timezone.utc)

    total_games = 0
    for team_key in TEAMS:
        try:
            games = await fetch_upcoming_games(team_key, days_ahead=14)
            cache.set(team_key, games)
            total_games += len(games)
            logger.info(f"  ✅ {TEAMS[team_key]['name']}: {len(games)} upcoming games cached")
        except Exception as e:
            logger.error(f"  ❌ Failed to refresh cache for {team_key}: {e}")

    # Also cache the combined "all" view
    try:
        all_games = []
        for team_key in TEAMS:
            cached = cache.get(team_key)
            if cached:
                all_games.extend(cached)
        all_games.sort(key=lambda g: g["date"])
        cache.set("all", all_games)
        logger.info(f"  ✅ Combined 'all' cache: {len(all_games)} games")
    except Exception as e:
        logger.error(f"  ❌ Failed to build combined cache: {e}")

    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    logger.info(f"💓 Heartbeat complete — {total_games} total games cached in {elapsed:.1f}s")


def start(run_immediately: bool = True) -> None:
    """
    Start the heartbeat scheduler.

    Args:
        run_immediately: If True, run a cache refresh on startup before
                         waiting for the first 06:00 UTC trigger.
    """
    scheduler.add_job(
        refresh_schedule_cache,
        trigger=CronTrigger(hour=6, minute=0, timezone="UTC"),
        id="daily_schedule_refresh",
        name="Daily Schedule Cache Refresh (Heartbeat)",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("💓 Heartbeat scheduler started — next run at 06:00 UTC daily")

    if run_immediately:
        logger.info("💓 Running initial cache warm-up on startup...")
        asyncio.create_task(refresh_schedule_cache())


def stop() -> None:
    """Shut down the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("💓 Heartbeat scheduler stopped")
