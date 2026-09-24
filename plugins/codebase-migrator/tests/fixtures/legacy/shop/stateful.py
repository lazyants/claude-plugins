_CACHE = {}
_COUNT = 0


def remember(key, value):
    _CACHE[key] = value
    return len(_CACHE)


def bump():
    global _COUNT
    _COUNT += 1
    return _COUNT
