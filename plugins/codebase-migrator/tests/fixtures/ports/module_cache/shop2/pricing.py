from shop2 import _memo
from shop2.money import round_money


class PricingError(ValueError):
    pass


def apply_discount(price, pct):
    key = (price, pct)
    if key in _memo._MEMO:
        return _memo._MEMO[key]
    if pct < 0 or pct > 100:
        raise PricingError("pct out of range")
    result = round_money(price * (100 - pct) / 100)
    _memo._MEMO[key] = result
    return result


def normalize_items(items):
    items.sort()


def dedupe(items):
    seen = set()
    i = 0
    while i < len(items):
        if items[i] in seen:
            del items[i]
        else:
            seen.add(items[i])
            i += 1
    return items


def describe(price):
    print("price:", price)
    return str(price)
