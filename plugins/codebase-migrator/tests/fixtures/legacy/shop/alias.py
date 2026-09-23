_REG = {}


def put(key, value):
    reg = _REG
    reg[key] = value
    return value
