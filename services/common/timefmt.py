from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def ist(ts: datetime | str) -> str:
    """Format a datetime or ISO timestamp for people in India. Naive values mean UTC."""
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    t = ts.astimezone(ZoneInfo("Asia/Kolkata"))
    return f"{t:%a} {t.day} {t:%b}, {t.hour % 12 or 12}:{t:%M} {t:%p} IST"
