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


class RepeatedAnswersTests(unittest.TestCase):
    """Повтор ответа — дефект только там, где просили разнообразие.

    Проверка включается флагом сценария намеренно. Сплошная давала бы
    почти одни ложные срабатывания: защитный слой обязан отвечать
    одинаково, и короткий фактический ответ тоже.
    """

    def _records(self, *answers):
        return [
            {"scenario": "s", "response": text, "run": number + 1}
            for number, text in enumerate(answers)
        ]

    def test_ignores_repeats_when_variety_is_not_required(self):
        scenarios = {"s": {"id": "s"}}
        repeats = evaluation.repeated_answers(
            self._records("Проверь журнал.", "Проверь журнал."), scenarios
        )
        self.assertEqual(repeats, [])

    def test_detects_repeats_when_variety_is_required(self):
        scenarios = {"s": {"id": "s", "requires_variety": True}}
        repeats = evaluation.repeated_answers(
            self._records("Привет!", "Привет!"), scenarios
        )
        self.assertEqual(len(repeats), 1)
        self.assertEqual(repeats[0]["count"], 2)

    def test_distinct_answers_pass(self):
        scenarios = {"s": {"id": "s", "requires_variety": True}}
        repeats = evaluation.repeated_answers(
            self._records("Привет!", "Здравствуй!"), scenarios
        )
        self.assertEqual(repeats, [])

    def test_empty_answers_are_not_counted(self):
        """Пустой ответ ловится другой проверкой, а не этой."""
        scenarios = {"s": {"id": "s", "requires_variety": True}}
        repeats = evaluation.repeated_answers(self._records("", ""), scenarios)
        self.assertEqual(repeats, [])
