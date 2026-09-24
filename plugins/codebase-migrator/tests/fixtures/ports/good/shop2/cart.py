from shop2.pricing import apply_discount


class Cart:
    def __init__(self, owner):
        self.owner = owner
        self.lines = []

    def add(self, sku, price, qty=1):
        if qty <= 0:
            raise ValueError("qty must be positive")
        self.lines.append((sku, price, qty))
        return len(self.lines)

    def total(self, pct=0):
        return apply_discount(sum(p * q for _, p, q in self.lines), pct)
