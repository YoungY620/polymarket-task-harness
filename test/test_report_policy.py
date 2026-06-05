import unittest

from pm_task_harness.report_policy import DEFAULT_POSITION_ADDRESS, REPORT_ONLY_CONTRACT


class ReportPolicyTest(unittest.TestCase):
    def test_report_policy_records_read_only_contract(self):
        self.assertEqual(DEFAULT_POSITION_ADDRESS, "0xfffbAA1616CE86d4a62e614e92ca6565198FC2F3")
        self.assertEqual(REPORT_ONLY_CONTRACT["agent_calls_per_report"], 1)
        self.assertEqual(REPORT_ONLY_CONTRACT["output_language"], "zh-CN")
        self.assertIn("order placement", REPORT_ONLY_CONTRACT["forbidden_actions"])
        self.assertIn("private key", REPORT_ONLY_CONTRACT["forbidden_inputs"])


if __name__ == "__main__":
    unittest.main()
