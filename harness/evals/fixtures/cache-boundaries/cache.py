"""Tiny cache used to test planning against known, executable behavior."""


class Cache:
    def __init__(self):
        self.entries = {}

    def put(self, key, value, now, ttl):
        self.entries[key] = (value, now + ttl)

    def get(self, key, now):
        record = self.entries.get(key)
        if record is None:
            return None
        value, expires = record
        if now > expires:
            return None
        if not value:
            return None
        return value
