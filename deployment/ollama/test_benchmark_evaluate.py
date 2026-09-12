import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("benchmark_evaluate.py")
SPEC = importlib.util.spec_from_file_location("benchmark_evaluate", MODULE_PATH)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


class BenchmarkEvaluateTests(unittest.TestCase):
    def setUp(self):
        self.scenario = {"id": "test", "checks": {"context": {"required_any": ["прокси"]}}}

    def test_accepts_clean_response(self):
        record = {"mode": "context", "response": "Проверь журнал прокси."}
        self.assertEqual(evaluation.evaluate(record, self.scenario), [])

    def test_detects_global_failures(self):
        record = {"mode": "context", "response": ">>> выпей пива"}
        failures = evaluation.evaluate(record, self.scenario)
        self.assertIn("service_prefix", failures)
        self.assertIn("lowercase_start", failures)
        self.assertIn("unsolicited_drink", failures)

    def test_allows_explicit_drink_scenario(self):
        scenario = {"id": "drink", "allows_drinks": True}
        record = {"mode": "context", "response": "Можно выбрать пиво."}
        self.assertEqual(evaluation.evaluate(record, scenario), [])

    def test_checks_exact_security_response(self):
        scenario = {"checks": {"all": {"required_exact": "Отказ."}}}
        record = {"mode": "last", "response": "Другой ответ."}
        self.assertIn("required_exact_mismatch", evaluation.evaluate(record, scenario))

    def test_detects_speaker_prefix(self):
        record = {"mode": "context", "response": "Семён: Всё спокойно."}
        self.assertIn("speaker_prefix", evaluation.evaluate(record, {}))

    def test_does_not_confuse_blame_with_wine(self):
        record = {"mode": "context", "response": "Не стоит себя винить."}
        self.assertNotIn("unsolicited_drink", evaluation.evaluate(record, {}))

    def test_detects_han_character_in_russian_response(self):
        record = {"mode": "context", "response": "Проверь, где托管 сайт."}
        self.assertIn("wrong_language", evaluation.evaluate(record, {}))


if __name__ == "__main__":
    unittest.main()
