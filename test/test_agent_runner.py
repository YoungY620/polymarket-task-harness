import tempfile
import unittest
from pathlib import Path

from pm_task_harness.agent_runner import AgentRunner


class AgentRunnerCommandTest(unittest.TestCase):
    def test_codex_initial_and_resume_commands_are_different(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = AgentRunner("codex", Path(tmp))
            initial = runner._codex_command(Path(tmp) / "out.txt", resume=False)
            resume = runner._codex_command(Path(tmp) / "out2.txt", resume=True)
        self.assertIn("-C", initial)
        self.assertIn("-s", initial)
        self.assertIn("resume", resume)
        self.assertNotIn("-C", resume)
        self.assertNotIn("-s", resume)

    def test_kimi_prompt_mode_does_not_use_yolo(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = AgentRunner("kimi", Path(tmp))
            command = runner._kimi_command("hello", resume=False)
        self.assertIn("-p", command)
        self.assertNotIn("-y", command)
        self.assertNotIn("--yolo", command)
        self.assertNotIn("--auto", command)


if __name__ == "__main__":
    unittest.main()
