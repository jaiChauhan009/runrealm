import math

XP_PER_KM = 10
XP_TERRITORY = 50
XP_HABIT = 5
XP_CHALLENGE_WIN = 100
STREAK_BONUS = 0.1   # +10 % per streak day, capped at 3×


def _streak_multiplier(streak: int) -> float:
    return min(1.0 + streak * STREAK_BONUS, 3.0)


def for_run(distance_km: float, current_streak: int) -> int:
    base = round(distance_km * XP_PER_KM)
    return round(base * _streak_multiplier(current_streak))


def for_territory() -> int:
    return XP_TERRITORY


def for_habit(current_streak: int) -> int:
    return round(XP_HABIT * _streak_multiplier(current_streak))


def for_challenge_win() -> int:
    return XP_CHALLENGE_WIN


def level_from_xp(total_xp: int) -> int:
    """level² × 100 = xp threshold  →  level = floor(sqrt(xp/100)), min 1."""
    level = 1
    while total_xp >= (level + 1) ** 2 * 100:
        level += 1
    return level


def xp_to_next_level(total_xp: int) -> int:
    current = level_from_xp(total_xp)
    return (current + 1) ** 2 * 100 - total_xp
