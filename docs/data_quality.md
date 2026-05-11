# Data Quality and Reconciliation Reference

This document maps the playbook's data quality rules and Appendix A decision rules to the implementation in `src/tools/csv_tools.py` and `src/tools/item_master.py`.

## Reason codes

Every shipment row produces a `reason_code` after reconciliation. These match the playbook's D8 reporting requirement.

| Reason code | Meaning | Origin |
|---|---|---|
| `exact_match` | item_id resolved to one canonical row, or item_name matched canonical name | D3, D6 (after name disambiguation) |
| `alias_match` | item_name resolved via A.2 alias table | D4 |
| `legacy_id_map` | item_id resolved via A.3 legacy/deprecated map | D5 |
| `special_case` | item_id is a special-case placeholder (e.g. 99999 clinical trial) | A.3 SPECIAL_CASE rule |
| `unresolved_conflict` | item_id is ambiguous and name did not disambiguate | D6 fallthrough |
| `excluded_unresolved` | nothing in the cascade resolved the row | All paths failed |

## Dispatchability distinction

Two flags are tracked separately:

- **`is_excluded`** — True if the master-data cascade failed (no canonical item could be identified)
- **`is_dispatchable`** — True only if `is_excluded` is False AND `unique_item_id` is non-blank AND passes the A.5 regex for its product_class

A row can be reconciled (master data resolved) yet still not dispatchable (missing or malformed unique_item_id). The report calls out both numbers because they're different operational problems.

## DQ rules implemented

- **DQ-01: unique_item_id missing or invalid** — surfaced as `missing_unique_id` (blank) and `invalid_unique_id_format` (regex failure) counts. Rows are kept in the reconciliation output but flagged as not dispatchable.
- **DQ-04: ambiguous item_id requires name disambiguation** — implemented in the D6 conflict branch. When item_id 10021 appears, item_name must distinguish RMD-100 (Remdesivir 100mg) from RMD-200 (Remdesivir 200mg). If the name doesn't help, the row is tagged `unresolved_conflict`.

## A.5 format patterns implemented

Regex patterns from playbook A.5, compiled at import time:

| Product class | Pattern |
|---|---|
| Antiviral | `^RMD-\d{4}-\d{4}$` |
| Oncology Biologic | `^PMB-\d{4}-\d{5}$` |
| Emergency | `^EPI-\d{4}-\d{4}$` |
| Controlled | `^CTRL-\d{4}-\d{6}$` |
| Respiratory | `^INH-\d{4}-\d{5}$` |
| Clinical Trial | `^CT-\d{4}-[A-Z0-9]{6}$` |

Product classes not in this table (Endocrine, Anticoagulant) have no format constraint per the playbook. Validation defaults to True for unknown product classes — we don't reject what we can't validate.

## Known issues surfaced by reconciliation

Running the system on the 14-day multi-corridor CSV reveals two real data quality issues worth noting:

- **47 rows have invalid unique_item_id format.** Many shipment records use product-specific prefixes (`INS-`, `MOR-`, `HEP-`) that don't conform to the playbook's standardized patterns. The playbook expects `CTRL-` for controlled substances, but the data uses `MOR-` for morphine. Either the playbook needs new format rules in A.5 for Endocrine/Anticoagulant product classes, or the shipment-record generation needs to align with existing CTRL- standard.
- **5 rows have blank unique_item_id.** Resolvable as items but not dispatchable until the barcode/serial is filled in. Concentrated 2 in C1 (Tier 1) and 1 in C2.

These findings appear in the leadership report and are exactly the kind of operational story reconciliation is supposed to surface.