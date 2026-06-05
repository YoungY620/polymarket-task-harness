"""Report-only operating policy for this harness."""

DEFAULT_POSITION_ADDRESS = "0xfffbAA1616CE86d4a62e614e92ca6565198FC2F3"

REPORT_ONLY_CONTRACT = {
    "position_source": "public Polymarket API wallet/profile address",
    "default_position_address": DEFAULT_POSITION_ADDRESS,
    "agent_calls_per_report": 1,
    "output_language": "zh-CN",
    "must_start_with": [
        "concise operation summary",
        "market URL",
        "existing-position or new-position label",
        "buy/sell direction",
        "amount",
    ],
    "forbidden_inputs": [
        "private key",
        "seed phrase",
        "wallet OTP",
        "trading API secret",
        "OKX/onchainos login session",
    ],
    "forbidden_actions": [
        "wallet signing",
        "order placement",
        "order cancellation",
        "fund transfer",
        "approval transaction",
    ],
}
