class _Settings:
    def __init__(self):
        self.rate = 1


SETTINGS = _Settings()


def set_rate(r):
    SETTINGS.rate = r
    return r
