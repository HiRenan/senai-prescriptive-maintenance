"""Process-independent Decimal contexts for banner data operations."""

from __future__ import annotations

from decimal import MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN, Context


def isolated_decimal_context(precision: int) -> Context:
    """Return a complete context that does not inherit process configuration."""

    return Context(
        prec=precision,
        rounding=ROUND_HALF_EVEN,
        Emin=MIN_EMIN,
        Emax=MAX_EMAX,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[],
    )
