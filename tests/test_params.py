"""Tests for httputil query-param parsing. Run: python -m unittest test_params"""

import unittest

import httputil


class IntParamTests(unittest.TestCase):
    def test_default_when_absent(self):
        self.assertEqual(httputil.int_param({}, "nfe", 16), 16)

    def test_default_when_empty_list(self):
        self.assertEqual(httputil.int_param({"nfe": []}, "nfe", 16), 16)

    def test_parses_valid(self):
        self.assertEqual(httputil.int_param({"nfe": ["8"]}, "nfe", 16), 8)

    def test_raises_on_garbage(self):
        with self.assertRaises(ValueError):
            httputil.int_param({"nfe": ["abc"]}, "nfe", 16)

    def test_raises_on_float_string(self):
        with self.assertRaises(ValueError):
            httputil.int_param({"nfe": ["1.5"]}, "nfe", 16)


class FloatParamTests(unittest.TestCase):
    def test_default_when_absent(self):
        self.assertEqual(httputil.float_param({}, "cfg", 0.5), 0.5)

    def test_parses_valid(self):
        self.assertEqual(httputil.float_param({"cfg": ["0.3"]}, "cfg", 0.5), 0.3)

    def test_parses_integer_string(self):
        self.assertEqual(httputil.float_param({"speed": ["1"]}, "speed", 1.0), 1.0)

    def test_raises_on_garbage(self):
        with self.assertRaises(ValueError):
            httputil.float_param({"cfg": ["hot"]}, "cfg", 0.5)


if __name__ == "__main__":
    unittest.main()
