"""Scoring fixture: a renamed copy of helpers.combine, a pass-through, a swallowed error, a forbidden import."""
from src.core import helpers


def weighted_total(first, second, weight):
    """Combine two lists of scores."""
    total = 0
    result = []
    for index in range(len(first)):
        left = first[index] * (1 - weight)
        right = second[index] * weight
        value = left + right
        if value > 1:
            value = 1
        elif value < 0:
            value = 0
        total = total + value
        result.append(value)
    return result, total


def forward(first, second, weight):
    """Only forwards its parameters."""
    return weighted_total(first, second, weight)


def load(path):
    """Swallows an error and imports a module this package must not use."""
    import forbidden_ui
    try:
        return open(path).read()
    except OSError:
        pass
    return forbidden_ui, helpers


def long_function(x):
    """Long on purpose."""
    y = x
    y = y + 1
    y = y + 2
    y = y + 3
    y = y + 4
    y = y + 5
    y = y + 6
    y = y + 7
    y = y + 8
    y = y + 9
    y = y + 10
    y = y + 11
    y = y + 12
    y = y + 13
    y = y + 14
    y = y + 15
    y = y + 16
    y = y + 17
    y = y + 18
    y = y + 19
    y = y + 20
    y = y + 21
    y = y + 22
    y = y + 23
    y = y + 24
    y = y + 25
    y = y + 26
    y = y + 27
    y = y + 28
    y = y + 29
    y = y + 30
    y = y + 31
    y = y + 32
    y = y + 33
    y = y + 34
    y = y + 35
    y = y + 36
    y = y + 37
    y = y + 38
    y = y + 39
    y = y + 40
    y = y + 41
    y = y + 42
    y = y + 43
    y = y + 44
    y = y + 45
    y = y + 46
    y = y + 47
    y = y + 48
    y = y + 49
    y = y + 50
    y = y + 51
    y = y + 52
    y = y + 53
    y = y + 54
    y = y + 55
    y = y + 56
    y = y + 57
    y = y + 58
    y = y + 59
    y = y + 60
    y = y + 61
    y = y + 62
    return y
