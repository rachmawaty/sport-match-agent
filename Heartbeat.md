# Heartbeat.md — Sport Match Agent

This file defines the autonomous schedule for the Sport Match Agent.
The agent "wakes up" at the intervals below to perform proactive background tasks.

---

## Heartbeat: `daily_schedule_refresh`

**Interval:** Every 24 hours
**Runs at:** 06:00 UTC

**What it does:**
1. Fetch upcoming game schedules for all four Boston teams (Patriots, Celtics, Bruins, Red Sox)
2. Refresh cached schedule data for the next 14 days
3. Log the number of upcoming games found
4. Surface a summary of the next 7 days of games (available via `/schedule` or MCP)

**What it does NOT do:**
- It does not generate match predictions
- It does not send notifications
- It does not call Claude or any AI model

> Predictions are **on-demand only** — triggered explicitly by a human or another agent via `predict_match`.
> See `Skills.md` for how to request a prediction.

---

## Notes

- If the ESPN API is unreachable during a heartbeat, the agent logs the failure and retries on the next cycle
- Schedule data is considered stale after 24 hours
- The heartbeat does not affect the availability of on-demand endpoints (`/predict`, `/schedule`, `/decide`)
