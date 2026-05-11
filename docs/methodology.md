# Methodology

## Architecture

The system is a LangGraph state machine with six nodes running linearly:

pdf_context → csv_analysis → weather → planner → report → email → END

For Option 3, we did **not** change the graph topology. There are no new nodes, no conditional edges, no cyclic loops. Our enhancement lives entirely inside the `csv_analysis` node, plus the prompts and state plumbing that surface the new data downstream.

This was a deliberate choice. Option 3 in the project brief is "Deep-Dive Trend Analysis" — its rubric points are about analytical depth, not architectural change. We invested the complexity budget in the analytics and reconciliation logic rather than re-wiring the graph.

## What changed in `csv_analysis`

The original `analyze_csv()` function did three things: load the CSV, compute generic statistics, and run IsolationForest anomaly detection on numeric columns. It returned a `CsvAnalysisResult` with summary/kpis/anomalies fields.

The enhanced version preserves that behavior (backward compatible) and adds two analytical stages before the existing logic runs:

**Stage 1: Item Master reconciliation.** Every shipment row is walked through a six-branch decision cascade based on the playbook's Appendix A:

| Branch | Trigger | Resulting reason code |
|---|---|---|
| D3 exact match | item_id resolves to exactly one canonical row | `exact_match` |
| D6 ambiguous | item_id maps to multiple canonicals (e.g. 10021 → RMD-100 or RMD-200) | Disambiguated by item_name; tagged `exact_match` or `alias_match` if name resolves, else `unresolved_conflict` |
| D5 legacy map | item_id is in A.3 legacy/deprecated table | `legacy_id_map` or `special_case` |
| D4 alias | item_name is in A.2 alias table | `alias_match` |
| Excluded | nothing matches | `excluded_unresolved` |

Implementation detail: the cascade prefers the legacy/special-case path over the name-match path when both succeed, because per the playbook D8 we must preserve reason-code visibility into deprecated identifier usage. Item ID 99999 (clinical trial placeholder) is intercepted at the exact-match step and re-tagged as `special_case` to ensure it surfaces in the operational report rather than being silently treated as a normal canonical row.

A separate step validates `unique_item_id` against the per-product-class regex in A.5 (DQ-01). A row can be master-data resolved but still not dispatchable if its unique_item_id is missing or malformed. This distinction is preserved in two fields: `is_excluded` (master data failed) and `is_dispatchable` (master data resolved AND unique_item_id valid).

**Stage 2: Period-over-period KPI computation.** The reconciled dataframe is split on the `planning_day` column: rows tagged `History` form the baseline window; rows tagged `Day0` or `Day1` form the current planning window. For each window, we compute KPIs per corridor:

- shipment_volume (count)
- dispatchable_volume (count of `is_dispatchable` rows)
- dispatchable_rate_pct
- cold_chain_volume (count where temp_control contains "Cold")
- cold_chain_share_pct
- excluded_volume + exclusion_rate_pct
- missing_unique_id count (resolved rows with blank unique_item_id)
- top_items by canonical_item_id (reconciliation makes this meaningful; without it, name variants fragment the count)

Deltas are computed in two flavors. Counts get percent change (`_delta_pct`); rates get percentage-point change (`_delta_pts`). The distinction matters for honest reporting: saying "exclusion rate went up 200%" is technically true if it went from 1% to 3%, but misleading. "Up 2 points (1% → 3%)" is honest.

## Item Master encoding

The playbook's Appendix A is encoded as Python data in `src/tools/item_master.py`. Five top-level structures:

- `CANONICAL_ITEMS` — list of 11 dicts from table A.1 (item_id, canonical_item_id, name, medicine_type, temp_control, product_class)
- `NAME_ALIASES` — 7 alias-to-canonical mappings from A.2
- `LEGACY_ID_MAP` — 4 legacy/deprecated/special-case ID mappings from A.3
- `SUBSTITUTIONS` — substitution rules from A.4 (documented but not auto-applied per D7)
- `UNIQUE_ID_PATTERNS` — compiled regex per product class from A.5

Helper indices (`_CANONICAL_BY_ITEM_ID`, `_CANONICAL_BY_NAME`, etc.) are built at import time for O(1) lookup. Name matching is normalized (lowercase, whitespace-collapsed) so "Remdesivir 100mg" and "Remdesivir  100mg" resolve to the same canonical.

We chose to encode the playbook as data rather than re-extract it from the PDF on each run because (a) it eliminates a class of LLM hallucination, (b) it's faster and more deterministic, and (c) it's testable. The agent stays "grounded in the playbook" because the data literally is the playbook's Appendix A.

## Prompt design

Three prompts were rewritten to use the new analytical outputs:

- **OPS_ANALYSIS_PROMPT** — instructs the OpsDataAgent on how to interpret reason codes, distinguish dispatchable-blockers from master-data failures, and frame period-over-period deltas accounting for window-size asymmetry (history covers 12 days, current covers 2)
- **PLANNER_PROMPT** — adds a required "Data-quality blockers" section and a required "Trend-driven adjustments" section, forcing the LLM to tie recommendations to specific numbers
- **REPORT_PROMPT** — specifies a real HTML table for period-over-period KPIs with `.delta-up` / `.delta-down` CSS classes for directional formatting, plus a required Data Quality & Reconciliation section with reason code counts

The PDF_CONTEXT_PROMPT was left unchanged. It still reads the playbook to extract general business context (SLAs, weather thresholds, capacity model), but the structured Item Master lookups happen via the Python module, not via RAG.

## State plumbing

Two new fields were added to `AppState`: `reconciliation_summary` and `period_kpis`. They are written by `node_csv_analysis` and read by `node_planner` and `node_report`. The three agent functions (`run_ops_agent`, `run_planner_agent`, `run_report_agent`) were updated to accept these as optional kwargs (defaulting to `None`) so backward compatibility with the legacy single-day CSV is preserved.