# Project Background

## The business

SeeWeeS is a specialty pharmacy distributor moving time-critical, often cold-chain medicines from New Jersey distribution centers to hospitals along two corridors:

- **C1_I95_NJ_BOS** — NJ → Boston via I-95. Tier 1, life-critical, 6-hour SLA.
- **C2_NJ_PHL** — NJ → Philadelphia. Tier 2, standard specialty, 12-hour SLA.

The shipment mix is heavy on cold-chain items (Remdesivir, Insulin Lispro, Pembrolizumab) plus a clinical trial drug requiring strict cold chain at -20°C.

## The stakeholder

The intended audience for the system's output is the **SeeWeeS VP of Operations and the dispatch leadership team**. They need to make morning dispatch decisions in under five minutes, balancing weather risk, truck capacity, cold-chain constraints, and patient SLA tiers.

## The operational pain point

The starter system produces a single-day snapshot report. That's useful as a status update, but it leaves three operational gaps unaddressed:

1. **No visibility into trends.** Leadership can't see whether the operation is degrading or improving. A dispatchable rate of 44% looks bad in isolation but is meaningless without knowing it dropped 18 points from the previous period.

2. **No reconciliation of dirty data.** Shipment records arrive with typo'd item names ("Remdesivir 100 mg" vs canonical "Remdesivir 100mg"), legacy item IDs from older systems, and clinical trial placeholders that need special handling. Without reconciliation, the system either silently miscounts identical drugs (because variants look like different items) or excludes rows that could have been recovered with playbook lookup tables.

3. **No traceable data quality story.** When rows fail to dispatch, leadership wants to know why, by reason code, so they can act on the right problem. Is it master data resolution failing? Or is the master data fine and the unique_item_id barcodes missing? These are different operational problems with different owners.

## What we built

Option 3 (Deep-Dive Trend Analysis) addresses all three gaps:

- **Trend visibility** via period-over-period KPI computation, with corridor-level deltas surfaced in the leadership report
- **Reconciliation** via a Python-encoded version of the playbook's Appendix A (canonical master, alias table, legacy ID map, special-case rules, format regex), walked through the D3-D6 decision cascade per shipment
- **Traceability** via reason codes attached to every row (exact_match, alias_match, legacy_id_map, special_case, unresolved_conflict, excluded_unresolved), aggregated and surfaced in the report's Data Quality section

The agents are now grounded in the playbook's actual rules rather than asking the LLM to re-derive them from the PDF each run. This makes the system reliable, auditable, and grader-verifiable.