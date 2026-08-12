"""Tests for httputil query-param parsing. Run: python -m unittest test_params"""

import unittest

import httputil


class FloatParamTests(unittest.TestCase):
    def test_default_when_absent(self):
        self.assertEqual(httputil.float_param({}, "cfg", 0.5), 0.5)

    def test_parses_valid(self):
        self.assertEqual(httputil.float_param({"cfg": ["0.3"]}, "cfg", 0.5), 0.3)

    def test_parses_integer_string(self):
        self.assertEqual(httputil.float_param({"cfg": ["1"]}, "cfg", 1.0), 1.0)

    def test_raises_on_garbage(self):
        with self.assertRaises(ValueError):
            httputil.float_param({"cfg": ["hot"]}, "cfg", 0.5)


if __name__ == "__main__":
    unittest.main()
