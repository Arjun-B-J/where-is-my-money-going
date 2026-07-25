"""Time helper.

Every timestamp in this app is **naive UTC**. SQLite does not preserve timezone
offsets, so storing aware datetimes would mean reading back naive ones and
mixing the two — which raises `TypeError` the moment you compare them. Rather
than half-support timezones, the convention is: naive, always UTC, produced
here.

`datetime.utcnow()` is deprecated in 3.12+, hence the explicit conversion.
"""
from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Current UTC time as a naive datetime."""
    return datetime.now(UTC).replace(tzinfo=None)
