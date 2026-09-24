def round_money(x):
    return round(x + 0.0, 2)


def to_cents(x):
    if x < 0:
        raise ValueError("negative amount")
    return int(round(x * 100))
