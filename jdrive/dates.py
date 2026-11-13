"""Times. The database always holds naive UTC; people always see Philippine time."""
from datetime import UTC, datetime, timedelta, timezone

MANILA = timezone(timedelta(hours=8))


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


def to_local(moment):
    """Naive UTC from the database -> an aware datetime in Philippine time."""
    return moment.replace(tzinfo=UTC).astimezone(MANILA)


def from_local(moment):
    """A naive Philippine time -> naive UTC (for the database)."""
    return moment.replace(tzinfo=MANILA).astimezone(UTC).replace(tzinfo=None)


def today():
    """Today's date in the Philippines."""
    return to_local(utcnow()).date()
