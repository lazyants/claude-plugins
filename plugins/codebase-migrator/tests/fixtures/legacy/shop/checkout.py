from shop.audit import record
from shop.pricing import apply_discount


def checkout(price, pct):
    total = apply_discount(price, pct)
    record(("checkout", total))
    return total
