"""The application's clock.

Everything the domain does with "now" — appointment slots, check-in windows,
reminders, plan status, report ranges, audit stamps — goes through ``now()``
and ``today()``. It is the **local wall clock, naive** (no tzinfo): an
appointment "on Monday at 08:00" is a wall-clock fact, and every stamp is
kept in the same frame so dates never shift across a timezone boundary.
(Only what leaves the system in a standard format — the JWT expiry and the
e-mail ``Date`` header — uses real UTC.)

The clock can be **travelled** (``travel_to``): from then on ``now()`` runs
from the given moment at the normal pace. The test suite anchors it at a
fixed instant so that results never depend on the date, the time of day or
the timezone of the machine running it.
"""

from datetime import date, datetime, timedelta

_offset = timedelta(0)


def now() -> datetime:
    """Current local wall-clock time, naive, with microseconds."""
    return datetime.now() + _offset


def today() -> date:
    return now().date()


def travel_to(moment: datetime) -> None:
    """Make ``now()`` read ``moment`` from this instant on (time keeps ticking)."""
    global _offset
    _offset = moment.replace(tzinfo=None) - datetime.now()


def reset() -> None:
    """Back to the real clock."""
    global _offset
    _offset = timedelta(0)
