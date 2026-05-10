"""
Unit tests for the Option 3 enhancements: Item Master reconciliation and
period-over-period KPI computation. These tests exercise the playbook's
Appendix A decision cascade (D3-D6) and verify that the KPI engine produces
sensible numbers across corridors and time periods.

Run from the project root:
    PYTHONPATH=src pytest tests/test_trend_analysis.py -v
"""
from __future__ import annotations
import pandas as pd
import pytest

# These imports require PYTHONPATH=src
from tools.csv_tools import (
    reconcile_dataframe,
    period_over_period,
    compute_corridor_kpis,
    split_into_periods,
)
from tools.item_master import (
    find_by_item_id,
    find_by_name,
    find_by_legacy_id,
    validate_unique_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _row(item_id, item_name, unique_id="RMD-2026-0001",
         corridor="C1_I95_NJ_BOS", planning_day="History"):
    """Helper to build a single shipment row for tests."""
    return {
        "shipment_date": "2026-02-22",
        "planning_day": planning_day,
        "is_planning_window": 0 if planning_day == "History" else 1,
        "corridor_id": corridor,
        "item_id": item_id,
        "item_name": item_name,
        "unique_item_id": unique_id,
        "dispatch_location": "Test-Hospital",
    }


# ---------------------------------------------------------------------------
# Item Master lookup tests
# ---------------------------------------------------------------------------
class TestItemMasterLookups:
    """Tests for the raw lookup helpers in item_master.py."""

    def test_canonical_lookup_by_unique_item_id(self):
        # 10022 has exactly one canonical row (INS-LIS)
        candidates = find_by_item_id(10022)
        assert len(candidates) == 1
        assert candidates[0]["canonical_item_id"] == "INS-LIS"

    def test_canonical_lookup_ambiguous_item_id(self):
        # 10021 has TWO canonical rows (RMD-100 and RMD-200) — name must disambiguate
        candidates = find_by_item_id(10021)
        assert len(candidates) == 2
        canon_ids = {c["canonical_item_id"] for c in candidates}
        assert canon_ids == {"RMD-100", "RMD-200"}

    def test_canonical_lookup_unknown_id(self):
        assert find_by_item_id(99000) == []

    def test_alias_resolves_space_variant(self):
        assert find_by_name("Remdesivir 100 mg") == "RMD-100"

    def test_alias_resolves_brand_in_parens(self):
        assert find_by_name("Pembrolizumab (Keytruda)") == "PMB-KEY"

    def test_alias_resolves_uk_spelling(self):
        assert find_by_name("Morphine Sulphate") == "MOR-SUL"

    def test_legacy_id_map_remdesivir(self):
        result = find_by_legacy_id(10020)
        assert result is not None
        assert result["canonical_item_id"] == "RMD-100"
        assert result["rule"] == "LEGACY_ID_MAP"

    def test_legacy_id_map_special_case(self):
        # 99999 is flagged SPECIAL_CASE for the clinical trial drug
        result = find_by_legacy_id(99999)
        assert result is not None
        assert result["rule"] == "SPECIAL_CASE"

    def test_unique_id_format_valid(self):
        # Antiviral pattern: ^RMD-\d{4}-\d{4}$
        assert validate_unique_id("RMD-2026-0001", "Antiviral") is True

    def test_unique_id_format_invalid(self):
        assert validate_unique_id("INS-2026-0001", "Antiviral") is False


# ---------------------------------------------------------------------------
# Reconciliation cascade tests
# ---------------------------------------------------------------------------
class TestReconciliationCascade:
    """Tests for the playbook D3-D6 decision cascade in reconcile_dataframe."""

    def test_exact_match(self):
        df = pd.DataFrame([_row(10022, "Insulin Lispro", "INS-2026-0001")])
        enriched, summary = reconcile_dataframe(df)
        row = enriched.iloc[0]
        assert row["canonical_item_id"] == "INS-LIS"
        assert row["reason_code"] == "exact_match"
        assert row["is_excluded"] is False or row["is_excluded"] == False

    def test_alias_match(self):
        # "Remdesivir 100 mg" with a space is in the alias table A.2
        df = pd.DataFrame([_row(10021, "Remdesivir 100 mg", "RMD-2026-0001")])
        enriched, _ = reconcile_dataframe(df)
        row = enriched.iloc[0]
        assert row["canonical_item_id"] == "RMD-100"
        assert row["reason_code"] == "alias_match"

    def test_legacy_id_map(self):
        # 10020 is a legacy ID for RMD-100 per A.3
        df = pd.DataFrame([_row(10020, "Remdesivir 100mg", "RMD-2026-0001")])
        enriched, _ = reconcile_dataframe(df)
        row = enriched.iloc[0]
        assert row["canonical_item_id"] == "RMD-100"
        assert row["reason_code"] == "legacy_id_map"

    def test_special_case_clinical_trial(self):
        # 99999 is the clinical trial placeholder; must be flagged SPECIAL_CASE
        df = pd.DataFrame([_row(99999, "Experimental Oncology Drug",
                                "CT-2026-A1B2C3")])
        enriched, _ = reconcile_dataframe(df)
        row = enriched.iloc[0]
        assert row["canonical_item_id"] == "EXP-ONC-CT"
        assert row["reason_code"] == "special_case"

    def test_excluded_unresolved(self):
        # Garbage item_id and name -> should be excluded
        df = pd.DataFrame([_row(88888, "Mystery Substance", "MYS-2026-0001")])
        enriched, summary = reconcile_dataframe(df)
        row = enriched.iloc[0]
        assert row["reason_code"] == "excluded_unresolved"
        assert summary["rows_excluded"] == 1

    def test_ambiguous_item_id_disambiguated_by_name(self):
        # item_id 10021 is ambiguous (RMD-100 vs RMD-200); the name picks RMD-200
        df = pd.DataFrame([_row(10021, "Remdesivir 200mg", "RMD-2026-0001")])
        enriched, _ = reconcile_dataframe(df)
        row = enriched.iloc[0]
        assert row["canonical_item_id"] == "RMD-200"

    def test_missing_unique_id_blocks_dispatch(self):
        # Row resolves cleanly but unique_item_id is blank -> not dispatchable
        df = pd.DataFrame([_row(10022, "Insulin Lispro", unique_id="")])
        enriched, summary = reconcile_dataframe(df)
        row = enriched.iloc[0]
        assert row["reason_code"] == "exact_match"  # master data is fine
        assert row["is_dispatchable"] is False or row["is_dispatchable"] == False
        assert summary["missing_unique_id"] == 1

    def test_summary_counts(self):
        # Six rows covering each branch
        df = pd.DataFrame([
            _row(10022, "Insulin Lispro", "INS-2026-0001"),         # exact
            _row(10021, "Remdesivir 100 mg", "RMD-2026-0001"),       # alias
            _row(10020, "Remdesivir 100mg", "RMD-2026-0001"),        # legacy
            _row(99999, "Experimental Onc Drug", "CT-2026-A1B2C3"),  # special
            _row(88888, "Mystery", "MYS-2026-0001"),                 # excluded
            _row(10022, "Insulin Lispro", ""),                       # missing UID
        ])
        _, summary = reconcile_dataframe(df)
        assert summary["rows_total"] == 6
        assert summary["rows_resolved"] == 5     # everything except Mystery
        assert summary["rows_excluded"] == 1     # Mystery
        assert summary["missing_unique_id"] == 1  # blank UID row
        # by_reason_code should contain all five codes that fired
        codes = set(summary["by_reason_code"].keys())
        assert codes == {"exact_match", "alias_match", "legacy_id_map",
                         "special_case", "excluded_unresolved"}


# ---------------------------------------------------------------------------
# Period-over-period tests
# ---------------------------------------------------------------------------
class TestPeriodOverPeriod:
    """Tests for split_into_periods, compute_corridor_kpis, period_over_period."""

    def _two_period_df(self):
        """Build a dataframe with both History and Day0 rows on both corridors."""
        rows = []
        # History: 4 C1 rows (3 dispatchable cold-chain Remdesivir + 1 EPI room-temp)
        for _ in range(3):
            rows.append(_row(10022, "Insulin Lispro", "INS-2026-0001",
                             corridor="C1_I95_NJ_BOS", planning_day="History"))
        rows.append(_row(10040, "Epinephrine Auto-Injector", "EPI-2026-0001",
                         corridor="C1_I95_NJ_BOS", planning_day="History"))
        # History: 2 C2 rows
        for _ in range(2):
            rows.append(_row(10070, "Albuterol Inhaler", "INH-2026-00001",
                             corridor="C2_NJ_PHL", planning_day="History"))
        # Day0: 2 C1, 1 C2
        rows.append(_row(10022, "Insulin Lispro", "INS-2026-0001",
                         corridor="C1_I95_NJ_BOS", planning_day="Day0"))
        rows.append(_row(10022, "Insulin Lispro", "INS-2026-0001",
                         corridor="C1_I95_NJ_BOS", planning_day="Day0"))
        rows.append(_row(10070, "Albuterol Inhaler", "INH-2026-00001",
                         corridor="C2_NJ_PHL", planning_day="Day0"))
        return pd.DataFrame(rows)

    def test_split_history_and_current(self):
        df = self._two_period_df()
        enriched, _ = reconcile_dataframe(df)
        history, current = split_into_periods(enriched)
        assert len(history) == 6
        assert len(current) == 3

    def test_corridor_kpis_has_both_corridors(self):
        df = self._two_period_df()
        enriched, _ = reconcile_dataframe(df)
        kpis = compute_corridor_kpis(enriched)
        assert "C1_I95_NJ_BOS" in kpis
        assert "C2_NJ_PHL" in kpis

    def test_corridor_kpis_tier_labels(self):
        df = self._two_period_df()
        enriched, _ = reconcile_dataframe(df)
        kpis = compute_corridor_kpis(enriched)
        assert kpis["C1_I95_NJ_BOS"]["tier"] == "Tier 1"
        assert kpis["C2_NJ_PHL"]["tier"] == "Tier 2"

    def test_corridor_kpis_cold_chain_share(self):
        # C1 has 4 history rows: 3 cold-chain (Insulin) + 1 room-temp (EPI)
        df = self._two_period_df()
        enriched, _ = reconcile_dataframe(df)
        history, _ = split_into_periods(enriched)
        kpis = compute_corridor_kpis(history)
        # 3 of 4 = 75%
        assert kpis["C1_I95_NJ_BOS"]["cold_chain_share_pct"] == 75.0

    def test_period_over_period_returns_expected_keys(self):
        df = self._two_period_df()
        enriched, _ = reconcile_dataframe(df)
        pop = period_over_period(enriched)
        expected_keys = {"history", "current", "deltas",
                         "history_window", "current_window"}
        assert expected_keys.issubset(pop.keys())

    def test_period_over_period_deltas_have_count_and_rate_flavors(self):
        df = self._two_period_df()
        enriched, _ = reconcile_dataframe(df)
        pop = period_over_period(enriched)
        c1_deltas = pop["deltas"]["C1_I95_NJ_BOS"]
        # Counts get _delta_pct
        assert "shipment_volume_delta_pct" in c1_deltas
        # Rates get _delta_pts
        assert "dispatchable_rate_pct_delta_pts" in c1_deltas

    def test_empty_dataframe_returns_empty(self):
        empty_df = pd.DataFrame(columns=[
            "shipment_date", "planning_day", "corridor_id",
            "item_id", "item_name", "unique_item_id",
        ])
        kpis = compute_corridor_kpis(empty_df)
        assert kpis == {}