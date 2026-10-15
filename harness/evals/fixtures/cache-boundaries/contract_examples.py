"""Executable acceptance criteria. Two failures are deliberately seeded."""
import unittest
from cache import Cache


class Contract(unittest.TestCase):
    def test_expiry_boundary(self):
        c = Cache();c.put('key', 'value', now=10, ttl=5)
        self.assertIsNone(c.get('key', now=15))
    def test_falsy_values_are_present(self):
        c = Cache();c.put('key', 0, now=10, ttl=5)
        self.assertEqual(c.get('key', now=11), 0)
    def test_live_value_is_unchanged(self):
        c = Cache();c.put('key', 'value', now=10, ttl=5)
        self.assertEqual(c.get('key', now=14), 'value')
    def test_missing_value(self):
        self.assertIsNone(Cache().get('missing', now=10))


if __name__ == '__main__':unittest.main()
