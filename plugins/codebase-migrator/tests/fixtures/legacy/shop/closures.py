def _make_counter():
    n = 0

    def step():
        nonlocal n
        n += 1
        return n

    return step


step = _make_counter()
