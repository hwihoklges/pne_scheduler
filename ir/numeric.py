"""Finite numeric domains, not equipment safety limits."""

import math


def finite_number(value: float, name: str, *, positive: bool = False,
                  nonnegative: bool = False) -> float:
    """Reject booleans, nonnumbers and overflow with a consistent ValueError."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number (not bool)")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if positive and result <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and result < 0:
        raise ValueError(f"{name} must be nonnegative")
    return result


def positive_integer(value: int, name: str) -> int:
    """Accept integral numeric counts only; never truncate a fractional count."""
    number = finite_number(value, name, positive=True)
    if number != int(number) or value != int(number):
        raise ValueError(f"{name} must be an integer")
    return int(number)