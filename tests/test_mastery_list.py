import tempfile
import unittest
from pathlib import Path

from set_catalog import MasteryList


class MasteryListTests(unittest.TestCase):
    def test_add_remove_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mastery.json"
            sets = MasteryList(path)
            self.assertTrue(sets.add("kronen_prime_set"))
            self.assertFalse(sets.add("kronen_prime_set"))
            sets.add("braton_prime_set")
            self.assertEqual(MasteryList(path).slugs, ["kronen_prime_set", "braton_prime_set"])
            self.assertTrue(sets.remove("kronen_prime_set"))
            self.assertFalse(sets.remove("kronen_prime_set"))
            self.assertEqual(MasteryList(path).slugs, ["braton_prime_set"])

    def test_corrupt_file_starts_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mastery.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(MasteryList(path).slugs, [])


if __name__ == "__main__":
    unittest.main()
