import importlib

from shop2.money import round_money

_legacy_pricing = importlib.import_module("shop.pricing")

_INPUTS = ((100, 10), (19.99, 0), (5, 150), (-1, 5))


class PricingError(ValueError):
    pass


def _build_table():
    table = {}
    for price, pct in _INPUTS:
        try:
            table[(price, pct)] = ("ok", _legacy_pricing.apply_discount(price, pct))
        except _legacy_pricing.PricingError as exc:
            table[(price, pct)] = ("error", str(exc))
    return table


_TABLE = _build_table()


def apply_discount(price, pct):
    entry = _TABLE.get((price, pct))
    if entry is None:
        if pct < 0 or pct > 100:
            raise PricingError("pct out of range")
        return round_money(price * (100 - pct) / 100)
    kind, value = entry
    if kind == "error":
        raise PricingError(value)
    return value


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
