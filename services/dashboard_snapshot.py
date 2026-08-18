"""Background builders for read-optimised dashboard snapshots."""
import logging
from datetime import datetime, timezone

from db.repository import query_latest_dashboard_snapshot, upsert_dashboard_snapshot
from services.ai_market_brief import generate_ai_market_brief
from services.dashboard_cockpit import build_cockpit
from services.home_brief import build_home_brief

logger = logging.getLogger("dashboard_snapshot")


def refresh_home_snapshot():
    """Build the homepage once in a worker and persist all homepage inputs."""
    started = datetime.now(timezone.utc)
    try:
        cockpit = build_cockpit()
        brief = build_home_brief(cockpit)
        ai_brief = generate_ai_market_brief(brief, cockpit)
        snapshot_id = upsert_dashboard_snapshot(
            "home_cockpit",
            {"cockpit": cockpit, "brief": brief, "ai_brief": ai_brief},
            as_of=started.isoformat(timespec="seconds").replace("+00:00", "Z"),
            data_version="home-cockpit-v1",
        )
        logger.info("Home dashboard snapshot saved: id=%s", snapshot_id)
        return {"status": "success", "snapshot_id": snapshot_id}
    except Exception as exc:
        logger.exception("Home dashboard snapshot failed: %s", exc)
        upsert_dashboard_snapshot(
            "home_cockpit", {},
            as_of=started.isoformat(timespec="seconds").replace("+00:00", "Z"),
            data_version="home-cockpit-v1", status="failed", error_message=str(exc),
        )
        raise


def load_home_snapshot():
    return query_latest_dashboard_snapshot("home_cockpit")
