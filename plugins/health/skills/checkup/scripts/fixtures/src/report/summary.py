"""Summary fixture: another package that imports helpers, so it should join their unit."""
from src.core.helpers import combine


def show(values):
    """Combines values for a report."""
    return combine(values, values, 0.5)
