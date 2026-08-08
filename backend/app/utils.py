"""Small, shared coercions for request data.

Every one of these exists because a bare ``int(...)``/``float(...)`` on
caller-supplied data 500s on input a real client sends by accident (an empty
box, a placeholder string, a list). Coercing at the boundary keeps handlers
readable and keeps a typo out of the 500 log. Named to match the sibling apps'
helpers so the three read the same.
"""
import math


def to_float(value, default=0.0):
    """A finite float, else `default`. Rejects NaN/inf as well as junk: an
    infinite amount silently drains stock wherever a quantity is compared."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def to_positive_float(value, default=None):
    """A finite, non-negative float, else `default` (None means 'invalid', so a
    caller can 422 rather than silently substituting a number)."""
    f = to_float(value, default=None) if value is not None else None
    if f is None:
        try:
            f = float(value)
        except (TypeError, ValueError):
            return default
    if not math.isfinite(f) or f < 0:
        return default
    return f


def to_int(value, default=0, lo=None, hi=None):
    """An int, else `default`, optionally clamped to [lo, hi]. Clamping (rather
    than erroring) is right for paging/limit params: a caller asking for 10**9
    rows wants "as many as you'll give me", not a 422."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n
