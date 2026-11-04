"""Regressions for the dependency-free schema test helper."""
import json
import math
import unittest
from pathlib import Path

from _jsonschema import validate


class SchemaValidatorTests(unittest.TestCase):
    def test_boolean_constants_do_not_accept_numbers(self):
        for value in (0, 0.0, {"flag": 0}, [0]):
            expected = {"flag": False} if isinstance(value, dict) else [False] if isinstance(value, list) else False
            with self.subTest(value=value):
                self.assertTrue(validate(value, {"const": expected}))
                self.assertTrue(validate(value, {"enum": [expected]}))
        self.assertEqual(validate(False, {"const": False}), [])
        self.assertEqual(validate(1.0, {"const": 1}), [])

    def test_unique_items_uses_json_equality(self):
        self.assertTrue(validate([1, 1.0], {"uniqueItems": True}))
        self.assertTrue(validate([{"a": 1, "b": 2}, {"b": 2, "a": 1}], {"uniqueItems": True}))
        self.assertEqual(validate([False, 0], {"uniqueItems": True}), [])

    def test_upper_bounds(self):
        for value, schema in ((2, {"maximum": 1}), ("ab", {"maxLength": 1}), ([1, 2], {"maxItems": 1})):
            with self.subTest(schema=schema):
                self.assertTrue(validate(value, schema))
        self.assertEqual(validate(1, {"maximum": 1}), [])
        self.assertEqual(validate("a", {"maxLength": 1}), [])
        self.assertEqual(validate([1], {"maxItems": 1}), [])

    def test_non_finite_values_are_not_json_numbers(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                self.assertTrue(validate(value, {"type": "number"}))

    def test_evaluator_metric_schema_rejects_negative_ratios(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "agent-eval-result.schema.json").read_text(encoding="utf-8"))
        for name in ("citation_precision", "required_citation_recall"):
            metric = schema["properties"][name]
            with self.subTest(metric=name):
                self.assertTrue(validate(-0.1, metric))
                self.assertTrue(validate(1.1, metric))
                for value in (None, 0, 1):
                    self.assertEqual(validate(value, metric), [])
