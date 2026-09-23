class Audit:
    events = []


def record(event):
    Audit.events.append(event)
    return len(Audit.events)
