"""Frozen T3 90-minute capture (FIX3). Does not change learning horizons or hourly funding."""
CAPTURE_MINUTES = 90
CAPTURE_SECONDS = CAPTURE_MINUTES * 60
CAPTURE_MS = CAPTURE_SECONDS * 1000
WATCHDOG_GRACE_SECONDS = 240  # Process-finalization allowance; not extra PAPER trading.


def deadline_ns(started_ns: int) -> int:
    return started_ns + CAPTURE_SECONDS * 1_000_000_000


def watchdog_expired(elapsed_seconds: float) -> bool:
    return elapsed_seconds > CAPTURE_SECONDS + WATCHDOG_GRACE_SECONDS


def format_countdown(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
