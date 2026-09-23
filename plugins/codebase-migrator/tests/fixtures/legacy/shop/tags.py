class Tags:
    seen = []

    def add(self, tag):
        self.seen.append(tag)
        return len(self.seen)
