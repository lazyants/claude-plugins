from shop.money import round_money


class PricingError(ValueError):
    pass


def apply_discount(price, pct):
    if pct < 0 or pct > 100:
        raise PricingError("pct out of range")
    return round_money(price * (100 - pct) / 100)


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
