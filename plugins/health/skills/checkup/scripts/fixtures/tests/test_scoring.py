"""Test fixture: mentions scoring, not helpers."""
from src.core import scoring

assert scoring.weighted_total([0.0], [1.0], 0.5)[0] == [0.5]
