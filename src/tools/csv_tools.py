from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Tuple, List
import pandas as pd
import numpy as np
from sklearn.ensemble import IsolationForest

from tools.item_master import (
    find_by_item_id,
    find_by_name,
    find_by_legacy_id,
    validate_unique_id,
    lookup_canonical,
)


@dataclass
class CsvAnalysisResult:
    # Existing fields (kept for backward compatibility with the rest of the
    # pipeline)
    summary: Dict[str, Any]
    kpis: Dict[str, Any]
    anomalies: pd.DataFrame
    cleaned_shape: Tuple[int, int]
    numeric_cols: List[str]

    # New fields added for Option 3 (Deep-Dive Trend Analysis)
    reconciliation_summary: Dict[str, Any]   # output of reconcile_dataframe(...)
    period_kpis: Dict[str, Any]              # output of period_over_period(...)
    reconciled_df: pd.DataFrame              # the enriched dataframe (for anomalies + debug)


def analyze_csv(csv_path: str) -> CsvAnalysisResult:
    """
    Load a shipment CSV, reconcile it against the Item Master (Appendix A),
    compute period-over-period KPIs by corridor, and run anomaly detection
    on the reconciled data.

    Returns a CsvAnalysisResult with both legacy fields (summary, kpis,
    anomalies) and new Option-3 fields (reconciliation_summary, period_kpis).
    """
    # ----- Load and basic clean -----
    df = pd.read_csv(csv_path)
    original_shape = df.shape

    df.columns = [c.strip() for c in df.columns]
    df = df.dropna(how="all").copy()

    # Try to parse anything that looks like a date column
    for c in df.columns:
        if "date" in c.lower() or "time" in c.lower():
            try:
                df[c] = pd.to_datetime(df[c], errors="ignore")
            except Exception:
                pass

    # ----- Reconciliation (Playbook Appendix A) -----
    # Only run if the CSV looks like a shipment file (has the columns we need).
    has_shipment_schema = all(
        col in df.columns for col in ("item_id", "item_name")
    )

    if has_shipment_schema:
        reconciled_df, reconciliation_summary = reconcile_dataframe(df)
    else:
        # Legacy/generic CSV path: skip reconciliation, just pass through.
        reconciled_df = df.copy()
        reconciliation_summary = {
            "rows_total": int(len(df)),
            "note": "Reconciliation skipped: CSV does not have shipment schema (item_id, item_name).",
        }

    # ----- Period-over-period KPIs -----
    # Requires both shipment schema and a planning_day column.
    if has_shipment_schema and "planning_day" in reconciled_df.columns:
        period_kpis = period_over_period(reconciled_df)
    else:
        period_kpis = {
            "note": "Period-over-period skipped: CSV does not have 'planning_day' column.",
        }

    # ----- Basic summary (legacy, kept for the existing prompt template) -----
    summary = {
        "rows_original": int(original_shape[0]),
        "cols_original": int(original_shape[1]),
        "rows_after_drop_empty": int(reconciled_df.shape[0]),
        "missingness_top": reconciled_df.isna().mean().sort_values(ascending=False).head(10).to_dict(),
        "column_dtypes": {c: str(t) for c, t in reconciled_df.dtypes.items()},
        "columns": list(reconciled_df.columns),
    }

    # ----- Legacy KPIs (kept for backward compatibility) -----
    kpis: Dict[str, Any] = {}
    numeric_cols = reconciled_df.select_dtypes(include=[np.number]).columns.tolist()

    if numeric_cols:
        kpis["numeric_columns_count"] = len(numeric_cols)
        kpis["rows_count"] = int(reconciled_df.shape[0])

    # Surface a few headline reconciliation numbers in the legacy `kpis` dict
    # too, so the existing report template can pick them up without changes.
    if has_shipment_schema:
        kpis["rows_reconciled"] = reconciliation_summary.get("rows_resolved", 0)
        kpis["rows_excluded"] = reconciliation_summary.get("rows_excluded", 0)
        kpis["rows_dispatchable"] = reconciliation_summary.get("rows_dispatchable", 0)
        kpis["resolution_rate_pct"] = reconciliation_summary.get("resolution_rate_pct", 0.0)

    # ----- Anomalies on reconciled numeric data -----
    anomalies = pd.DataFrame()
    if len(numeric_cols) >= 2 and reconciled_df.shape[0] >= 20:
        X = reconciled_df[numeric_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0).values
        model = IsolationForest(
            n_estimators=200,
            contamination=0.03,
            random_state=42,
        )
        preds = model.fit_predict(X)
        scores = model.decision_function(X)

        df_anom = reconciled_df.copy()
        df_anom["is_anomaly"] = (preds == -1)
        df_anom["anomaly_score"] = scores

        anomalies = df_anom[df_anom["is_anomaly"]].sort_values("anomaly_score").head(25)

    return CsvAnalysisResult(
        summary=summary,
        kpis=kpis,
        anomalies=anomalies,
        cleaned_shape=reconciled_df.shape,
        numeric_cols=numeric_cols,
        reconciliation_summary=reconciliation_summary,
        period_kpis=period_kpis,
        reconciled_df=reconciled_df,
    )
    

# ---------------------------------------------------------------------------
# Item Master Reconciliation (Playbook Appendix A, Decision Rules D1–D8)
# ---------------------------------------------------------------------------
# Each shipment row is walked through the cascade:
#   D3 exact_match  -> (item_id, item_name) resolves cleanly to one canonical
#   D4 alias_match  -> item_name matches an A.2 alias
#   D5 legacy_id_map-> item_id matches an A.3 legacy/deprecated/placeholder id
#   D6 conflict     -> item_id is ambiguous AND name doesn't disambiguate
#   excluded_unresolved -> nothing matches
#
# Then unique_item_id is validated per A.5 (regex by product_class). Per
# DQ-01, rows with missing unique_item_id are excluded from planning but
# logged. We surface this as a separate flag so the OpsDataAgent can narrate
# both the master-data resolution AND the identifier hygiene story.


# Valid reason codes (matches playbook D8)
REASON_CODES = {
    "exact_match",         # D3
    "alias_match",         # D4
    "legacy_id_map",       # D5
    "special_case",        # A.3 SPECIAL_CASE rule (e.g. clinical trial placeholder)
    "unresolved_conflict", # D6 (item_id ambiguous, name doesn't help)
    "excluded_unresolved", # nothing matched
}


def _reconcile_row(row: pd.Series) -> Dict[str, Any]:
    """
    Reconcile a single shipment row to the canonical item master.
    Returns a dict with: canonical_item_id, canonical_item_name, temp_control,
    product_class, reason_code, reason_detail, unique_id_valid, is_excluded.
    """
    item_id_raw = row.get("item_id")
    item_name_raw = row.get("item_name", "")
    unique_id = row.get("unique_item_id", "")

    # Default "unresolved" result; we'll overwrite as we resolve.
    result: Dict[str, Any] = {
        "canonical_item_id": None,
        "canonical_item_name": None,
        "temp_control": None,
        "product_class": None,
        "reason_code": "excluded_unresolved",
        "reason_detail": "",
        "unique_id_valid": False,
        "is_excluded": True,
    }

    # ---- D3 / D5: try item_id-based resolution first ----
    candidates = find_by_item_id(item_id_raw)

    if len(candidates) == 1:
        # Exactly one canonical row for this item_id. Usually an EXACT_MATCH,
        # but if the same item_id also has a SPECIAL_CASE entry in the legacy
        # map (e.g. 99999 = clinical trial placeholder), surface that instead
        # so the report flags it for the operational care it requires.
        canon = candidates[0]
        legacy = find_by_legacy_id(item_id_raw)
        if legacy is not None and legacy["rule"] == "SPECIAL_CASE":
            result.update({
                "canonical_item_id": canon["canonical_item_id"],
                "canonical_item_name": canon["canonical_item_name"],
                "temp_control": canon["temp_control"],
                "product_class": canon["product_class"],
                "reason_code": "special_case",
                "reason_detail": legacy["rationale"],
                "is_excluded": False,
            })
        else:
            result.update({
                "canonical_item_id": canon["canonical_item_id"],
                "canonical_item_name": canon["canonical_item_name"],
                "temp_control": canon["temp_control"],
                "product_class": canon["product_class"],
                "reason_code": "exact_match",
                "reason_detail": f"item_id={item_id_raw} resolved to {canon['canonical_item_id']}",
                "is_excluded": False,
            })

    elif len(candidates) > 1:
        # D6: item_id ambiguous (e.g. 10021 -> RMD-100 or RMD-200).
        # Try to disambiguate using item_name (via A.1 canonical names or A.2 aliases).
        name_match = find_by_name(item_name_raw)
        if name_match and any(c["canonical_item_id"] == name_match for c in candidates):
            canon = lookup_canonical(name_match)
            # If name match was via an alias, label it that way; otherwise exact.
            from tools.item_master import _ALIASES_NORM, _norm
            is_alias = _norm(item_name_raw) in _ALIASES_NORM
            result.update({
                "canonical_item_id": canon["canonical_item_id"],
                "canonical_item_name": canon["canonical_item_name"],
                "temp_control": canon["temp_control"],
                "product_class": canon["product_class"],
                "reason_code": "alias_match" if is_alias else "exact_match",
                "reason_detail": f"item_id={item_id_raw} ambiguous; disambiguated via name '{item_name_raw}'",
                "is_excluded": False,
            })
        else:
            result.update({
                "reason_code": "unresolved_conflict",
                "reason_detail": f"item_id={item_id_raw} matches {len(candidates)} canonical items; name '{item_name_raw}' did not disambiguate",
                "is_excluded": True,
            })

    else:
        # No canonical row for this item_id. Try legacy first (D5), then name (D4).
        # We check legacy BEFORE name because a legacy/deprecated item_id is a
        # stronger signal of what the system intended than a name match, and
        # per playbook D8 we need to report the row as fixed via legacy mapping.
        legacy = find_by_legacy_id(item_id_raw)
        if legacy is not None:
            canon = lookup_canonical(legacy["canonical_item_id"])
            code = "special_case" if legacy["rule"] == "SPECIAL_CASE" else "legacy_id_map"
            result.update({
                "canonical_item_id": canon["canonical_item_id"],
                "canonical_item_name": canon["canonical_item_name"],
                "temp_control": canon["temp_control"],
                "product_class": canon["product_class"],
                "reason_code": code,
                "reason_detail": legacy["rationale"],
                "is_excluded": False,
            })
        else:
            # D4: alias / canonical name lookup
            name_match = find_by_name(item_name_raw)
            if name_match:
                canon = lookup_canonical(name_match)
                from tools.item_master import _CANONICAL_BY_NAME, _norm
                # Distinguish A.1 canonical-name match (exact) from A.2 alias match.
                is_exact_name = _norm(item_name_raw) in _CANONICAL_BY_NAME
                result.update({
                    "canonical_item_id": canon["canonical_item_id"],
                    "canonical_item_name": canon["canonical_item_name"],
                    "temp_control": canon["temp_control"],
                    "product_class": canon["product_class"],
                    "reason_code": "exact_match" if is_exact_name else "alias_match",
                    "reason_detail": f"resolved via name '{item_name_raw}'",
                    "is_excluded": False,
                })
            # else: stays as excluded_unresolved with default values

    # ---- DQ-01 / A.5: validate unique_item_id format ----
    # Only meaningful if we resolved a product_class.
    if result["product_class"]:
        if unique_id is None or (isinstance(unique_id, float) and pd.isna(unique_id)) or str(unique_id).strip() == "":
            result["unique_id_valid"] = False
            # Per DQ-01, missing unique_item_id excludes the row from planning
            # even if the item itself was resolved. We track this separately:
            # the row is still "reconciled" but not "dispatchable".
        else:
            result["unique_id_valid"] = validate_unique_id(str(unique_id), result["product_class"])

    return result


def reconcile_dataframe(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Reconcile every row in `df` against the playbook Item Master (Appendix A).
    Adds new columns: canonical_item_id, canonical_item_name, temp_control,
    product_class, reason_code, reason_detail, unique_id_valid, is_excluded,
    is_dispatchable.

    Returns (enriched_df, summary). Summary is a dict suitable for prompts
    and the leadership report.
    """
    if df.empty:
        return df, {
            "rows_total": 0,
            "by_reason_code": {},
            "rows_resolved": 0,
            "rows_excluded": 0,
            "missing_unique_id": 0,
            "invalid_unique_id_format": 0,
            "rows_dispatchable": 0,
            "resolution_rate_pct": 0.0,
        }

    enriched = df.copy()
    recon = enriched.apply(_reconcile_row, axis=1, result_type="expand")
    enriched = pd.concat([enriched, recon], axis=1)

    # "Dispatchable" = master-data resolved AND unique_item_id valid.
    enriched["is_dispatchable"] = (~enriched["is_excluded"]) & (enriched["unique_id_valid"])

    rows_total = int(len(enriched))
    by_reason = enriched["reason_code"].value_counts().to_dict()
    rows_resolved = int((~enriched["is_excluded"]).sum())
    rows_excluded = int(enriched["is_excluded"].sum())

    # Identify the two DQ-01 failure modes separately so the report can speak
    # to data hygiene precisely.
    missing_uid = int(
        enriched[~enriched["is_excluded"]]
        .apply(
            lambda r: r["unique_item_id"] is None
            or (isinstance(r["unique_item_id"], float) and pd.isna(r["unique_item_id"]))
            or str(r["unique_item_id"]).strip() == "",
            axis=1,
        )
        .sum()
    )
    invalid_uid_fmt = int(((~enriched["is_excluded"]) & (~enriched["unique_id_valid"])).sum()) - missing_uid
    invalid_uid_fmt = max(invalid_uid_fmt, 0)

    rows_dispatchable = int(enriched["is_dispatchable"].sum())
    resolution_rate = round(100.0 * rows_resolved / rows_total, 1) if rows_total else 0.0

    summary = {
        "rows_total": rows_total,
        "by_reason_code": by_reason,
        "rows_resolved": rows_resolved,
        "rows_excluded": rows_excluded,
        "missing_unique_id": missing_uid,
        "invalid_unique_id_format": invalid_uid_fmt,
        "rows_dispatchable": rows_dispatchable,
        "resolution_rate_pct": resolution_rate,
    }
    return enriched, summary


# ---------------------------------------------------------------------------
# Period-over-Period Trend Analysis
# ---------------------------------------------------------------------------
# Computes KPIs per corridor for two time windows (History vs current
# planning window: Day0 + Day1), then surfaces the deltas. The OpsDataAgent
# narrates these trends to leadership.
#
# Assumes the dataframe has been reconciled by reconcile_dataframe() first,
# so canonical_item_id, temp_control, product_class, is_dispatchable, etc.
# are populated.


# Corridors with their SLA tier per playbook Section 7.
# C1_I95_NJ_BOS = Tier 1 (life-critical, 6h SLA)
# C2_NJ_PHL     = Tier 2 (standard specialty, 12h SLA)
CORRIDOR_TIER = {
    "C1_I95_NJ_BOS": "Tier 1",
    "C2_NJ_PHL":     "Tier 2",
}


def _pct(part: int, whole: int) -> float:
    """Safe percentage helper, returns 0.0 on divide-by-zero."""
    return round(100.0 * part / whole, 1) if whole else 0.0


def _delta_pct(current: float, baseline: float) -> float:
    """
    Period-over-period percent change. Returns None when baseline is 0 so the
    report can render "n/a" instead of a misleading "inf%".
    """
    if baseline == 0:
        return None
    return round(100.0 * (current - baseline) / baseline, 1)


def compute_corridor_kpis(df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """
    Compute KPIs per corridor for a (reconciled) shipment dataframe.

    Returns a dict like:
      {
        "C1_I95_NJ_BOS": {
            "tier": "Tier 1",
            "shipment_volume": 48,
            "dispatchable_volume": 35,
            "dispatchable_rate_pct": 72.9,
            "cold_chain_volume": 31,
            "cold_chain_share_pct": 64.6,
            "excluded_volume": 0,
            "exclusion_rate_pct": 0.0,
            "missing_unique_id": 2,
            "top_items": {"RMD-100": 12, "INS-LIS": 9, ...},
        },
        ...
      }
    """
    out: Dict[str, Dict[str, Any]] = {}

    if df.empty or "corridor_id" not in df.columns:
        return out

    for corridor, sub in df.groupby("corridor_id"):
        total = int(len(sub))
        dispatchable = int(sub["is_dispatchable"].sum()) if "is_dispatchable" in sub.columns else 0
        excluded = int(sub["is_excluded"].sum()) if "is_excluded" in sub.columns else 0

        # Cold-chain volume = anything whose temp_control implies refrigeration.
        # Per playbook A.1, the cold values are "Cold (2-8C)" and
        # "Strict Cold Chain (-20C)". Room-temp and Controlled Storage are not
        # treated as cold-chain for truck-type allocation.
        if "temp_control" in sub.columns:
            is_cold = sub["temp_control"].fillna("").str.contains("Cold", case=False, na=False)
            cold_chain = int(is_cold.sum())
        else:
            cold_chain = 0

        # Missing unique_item_id among resolved rows (DQ-01 hit)
        if "unique_item_id" in sub.columns:
            missing_uid = int(
                sub.apply(
                    lambda r: (
                        not r.get("is_excluded", True)  # resolved
                        and (
                            r["unique_item_id"] is None
                            or (isinstance(r["unique_item_id"], float) and pd.isna(r["unique_item_id"]))
                            or str(r["unique_item_id"]).strip() == ""
                        )
                    ),
                    axis=1,
                ).sum()
            )
        else:
            missing_uid = 0

        # Top items by volume (canonical key so name variants don't fragment counts)
        if "canonical_item_id" in sub.columns:
            top_items = (
                sub["canonical_item_id"].dropna().value_counts().head(5).to_dict()
            )
            # Convert numpy ints to plain ints for clean JSON-ish output
            top_items = {k: int(v) for k, v in top_items.items()}
        else:
            top_items = {}

        out[corridor] = {
            "tier": CORRIDOR_TIER.get(corridor, "Unknown"),
            "shipment_volume": total,
            "dispatchable_volume": dispatchable,
            "dispatchable_rate_pct": _pct(dispatchable, total),
            "cold_chain_volume": cold_chain,
            "cold_chain_share_pct": _pct(cold_chain, total),
            "excluded_volume": excluded,
            "exclusion_rate_pct": _pct(excluded, total),
            "missing_unique_id": missing_uid,
            "top_items": top_items,
        }

    return out


def split_into_periods(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split the reconciled dataframe into (history_df, current_df) using the
    `planning_day` column.

    History  = rows where planning_day == 'History' (baseline period)
    Current  = rows where planning_day in ('Day0', 'Day1') (planning window)

    Falls back to an empty current_df if the dataset lacks Day0/Day1 markers
    (e.g., when fed the legacy single-day CSV).
    """
    if "planning_day" not in df.columns:
        return df.iloc[0:0].copy(), df.copy()

    history = df[df["planning_day"] == "History"].copy()
    current = df[df["planning_day"].isin(["Day0", "Day1"])].copy()
    return history, current


def period_over_period(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Compute period-over-period KPIs by corridor. Returns:
      {
        "history": {<corridor>: {kpis...}, ...},
        "current": {<corridor>: {kpis...}, ...},
        "deltas":  {<corridor>: {<kpi_name>_delta_pct: <number or None>, ...}, ...},
        "history_window": {"rows": N, "label": "History (baseline)"},
        "current_window": {"rows": N, "label": "Day0 + Day1 (planning window)"},
      }
    """
    history, current = split_into_periods(df)

    history_kpis = compute_corridor_kpis(history)
    current_kpis = compute_corridor_kpis(current)

    # KPIs we want delta-tracked. (Counts get a percent-change; rates get a
    # point-change because pct-change of a pct is confusing for leadership.)
    delta_count_keys = ["shipment_volume", "dispatchable_volume", "cold_chain_volume"]
    delta_rate_keys  = ["dispatchable_rate_pct", "cold_chain_share_pct", "exclusion_rate_pct"]

    deltas: Dict[str, Dict[str, Any]] = {}
    all_corridors = sorted(set(history_kpis.keys()) | set(current_kpis.keys()))

    for corridor in all_corridors:
        h = history_kpis.get(corridor, {})
        c = current_kpis.get(corridor, {})
        d: Dict[str, Any] = {"tier": CORRIDOR_TIER.get(corridor, "Unknown")}

        for key in delta_count_keys:
            h_val = h.get(key, 0)
            c_val = c.get(key, 0)
            d[f"{key}_history"] = h_val
            d[f"{key}_current"] = c_val
            d[f"{key}_delta_pct"] = _delta_pct(c_val, h_val)

        for key in delta_rate_keys:
            h_val = h.get(key, 0.0)
            c_val = c.get(key, 0.0)
            d[f"{key}_history"] = h_val
            d[f"{key}_current"] = c_val
            d[f"{key}_delta_pts"] = round(c_val - h_val, 1)  # percentage-point change

        deltas[corridor] = d

    return {
        "history": history_kpis,
        "current": current_kpis,
        "deltas": deltas,
        "history_window": {"rows": int(len(history)), "label": "History (baseline)"},
        "current_window": {"rows": int(len(current)), "label": "Day0 + Day1 (planning window)"},
    }