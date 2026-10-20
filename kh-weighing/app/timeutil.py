from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .deps import ApiError


def parse_day(value: str | None, tz: str) -> date:
    if not value:
        return datetime.now(ZoneInfo(tz)).date()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ApiError(422, "bad_date", "Дата в формате ГГГГ-ММ-ДД", field="date")


def day_bounds(day: date, tz: str) -> tuple[datetime, datetime]:
    """Начало и конец суток по местному времени весовой (полуинтервал [start, end))."""
    zone = ZoneInfo(tz)
    start = datetime.combine(day, time(0, 0), tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=zone)
    return start, end


def year_bounds(year: int, tz: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(tz)
    return (
        datetime(year, 1, 1, tzinfo=zone),
        datetime(year + 1, 1, 1, tzinfo=zone),
    )
