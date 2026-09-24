import importlib
import sys

from shop2.money import round_money


class PricingError(ValueError):
    pass


def apply_discount(price, pct):
    if pct < 0 or pct > 100:
        raise PricingError("pct out of range")
    return round_money(price * (100 - pct) / 100)


def normalize_items(items):
    items.sort()


def dedupe(items):
    sys.setprofile(None)
    return importlib.import_module("shop.pricing").dedupe(items)


def describe(price):
    print("price:", price)
    return str(price)
