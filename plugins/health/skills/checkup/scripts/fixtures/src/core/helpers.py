"""Helpers fixture: imports scoring at module level (a cycle) and holds a renamed copy of weighted_total."""
from src.core import scoring


def combine(a, b, t):
    """Renamed copy of scoring.weighted_total."""
    acc = 0
    out = []
    for i in range(len(a)):
        lo = a[i] * (1 - t)
        hi = b[i] * t
        v = lo + hi
        if v > 1:
            v = 1
        elif v < 0:
            v = 0
        acc = acc + v
        out.append(v)
    return out, acc


def uses_scoring():
    """Calls scoring."""
    return scoring.forward([1], [2], 0.5)
