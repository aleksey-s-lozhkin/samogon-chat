import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("benchmark_dialogues.py")
SPEC = importlib.util.spec_from_file_location("benchmark_dialogues", MODULE_PATH)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class BenchmarkDialoguesTests(unittest.TestCase):
    def setUp(self):
        self.scenario = {
            "messages": [
                {"role": "user", "speaker": "guest_1", "content": "Первый вопрос"},
                {"role": "assistant", "content": "Первый ответ"},
                {"role": "user", "speaker": "guest_1", "content": "Уточнение"},
            ]
        }

    def test_last_mode_reproduces_single_message_request(self):
        messages = benchmark.build_messages("system", self.scenario, "last")

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0], {"role": "system", "content": "system"})
        self.assertIn("Гость @guest_1: Уточнение", messages[1]["content"])

    def test_context_mode_keeps_dialogue_order_and_roles(self):
        messages = benchmark.build_messages("system", self.scenario, "context")

        self.assertEqual([message["role"] for message in messages], [
            "system",
            "user",
            "assistant",
            "user",
        ])
        self.assertEqual(messages[2]["content"], "Первый ответ")

    def test_scenarios_file_is_valid_and_unique(self):
        scenarios = benchmark.load_scenarios(
            Path(__file__).with_name("benchmark-dialogues.json")
        )

        identifiers = [scenario["id"] for scenario in scenarios]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertGreaterEqual(len(scenarios), 8)
        for scenario in scenarios:
            self.assertTrue(scenario["messages"])
            self.assertIn("description", scenario)

    def test_second_prompt_candidate_keeps_critical_boundaries(self):
        prompt = (
            Path(__file__).parents[1]
            / "../chat/services/prompts/semen-candidate-v2.txt"
        ).resolve().read_text(encoding="utf-8")

        self.assertIn("Никогда не показывай", prompt)
        self.assertIn("Не угадывай настоящее имя", prompt)
        self.assertIn("не предлагает\nнапиток без просьбы", prompt)
        self.assertIn("префикса «>>>»", prompt)


if __name__ == "__main__":
    unittest.main()
