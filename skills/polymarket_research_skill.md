# Polymarket Research Skill

Use this skill for exactly one market selected by the static market filter.

## Workflow

1. Define the task strictly from the market resolution standard.
   - Use the supplied Polymarket URL and market description as the starting point.
   - If live lookup is available, verify the market's resolution rules and additional context.
   - Do not invent a different decision standard.

2. Decompose the event.
   - Identify the base rate or natural prior.
   - Break the event into concrete necessary or sufficient sub-events when useful.
   - Prefer explicit conditional reasoning over vague narrative.

3. Gather evidence.
   - Use official sources first, then reputable news, then expert/forecasting sources, then social/commentary.
   - Keep sources few but high quality.
   - Record how each source changes the probability.

4. Estimate probability.
   - Start from the base rate or a clearly labeled prior.
   - Update with each structured evidence row.
   - Produce a final probability for Yes and infer No as needed.

5. Compare with market price.
   - Use the supplied outcome_prices for Yes and No.
   - Buy Yes only when Yes has enough positive edge.
   - Buy No only when No has enough positive edge.
   - Otherwise skip.

## Output Contract

Return only strict JSON, with no markdown fences.

The JSON must include:
- `decision`: `buy_yes`, `buy_no`, or `skip`
- `outcome`: `Yes` or `No`
- `token_id`: selected CLOB token id, or empty string for skip
- `ai_prob`: probability for the chosen outcome, 0 to 1
- `market_prob`: Polymarket price for the chosen outcome, 0 to 1
- `edge`: `ai_prob - market_prob`
- `confidence`: `low`, `medium`, or `high`
- `definition.resolution_rule_summary`
- `definition.decision_standard`
- `evidence_table`: at least base, one evidence update, and final
- `sources`: at least two source objects with URL and key point

If the resolution standard is unclear, sources are weak, or absolute edge is below 8 percentage points, return `decision: "skip"`.
