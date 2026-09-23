import time


def stamp(label):
    return "%s@%d" % (label, int(time.time()))
