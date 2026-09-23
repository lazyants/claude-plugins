from shop import money


def call_named(name, x):
    return getattr(money, name)(x)
