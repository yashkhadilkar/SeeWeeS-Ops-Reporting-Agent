# SeeWeeS Ops Reporting Agent — Option 3: Deep-Dive Trend Analysis

UCLA MSBA AI Agents Project Challenge 2026
**House B, Team 1**
John Andrews · Yash Khadilkar · Qianhui Liang · Hanson Yang · Yifan Yao · Jingke Zhang

A LangGraph multi-agent system that turns raw shipment data into an executive-ready dispatch report for a specialty pharmacy distributor.

## What this project does

The starter system was a linear pipeline: it read a PDF playbook, analyzed a single-day shipment CSV, fetched weather, and produced a snapshot report. Our enhancement (**Option 3: Deep-Dive Trend Analysis**) makes the system meaningfully smarter in two ways:

- **Item Master reconciliation.** Every shipment row is walked through the playbook's Appendix A decision cascade — exact match, alias match, legacy ID map, special case, conflict, or excluded. Dirty data (typo'd names, deprecated IDs, clinical trial placeholders) is automatically resolved against canonical items, with traceable reason codes.
- **Period-over-period trend analysis.** Shipments are split into a baseline window (`History`) and a planning window (`Day0 + Day1`), then KPIs are computed per corridor (volume, dispatchable rate, cold-chain share) with explicit deltas. The agents narrate the *change* in operations, not just the snapshot.

Result: a report that leads with "Tier 1 corridor dispatchable rate dropped 18.1 points" instead of "the system has 129 rows."

See `docs/methodology.md` for the architectural detail.

## Project structure

```
SeeWeeS-Ops-Reporting-Agent/
├── data/                              # Original single-day inputs
├── data-for-enhancement/              # 14-day multi-corridor inputs + Item Master
├── src/
│   ├── main.py                        # Entry point
│   ├── graph.py                       # LangGraph wiring
│   ├── agents.py                      # LLM agents
│   ├── prompts.py                     # Prompt templates
│   └── tools/
│       ├── item_master.py             # Playbook Appendix A as Python data
│       ├── csv_tools.py               # Reconciliation + period-over-period
│       ├── pdf_tools.py
│       ├── weather_tools.py
│       └── email_tools.py
├── tests/
│   ├── conftest.py                    # Adds src/ to sys.path for tests
│   ├── test_trend_analysis.py         # 25 tests covering reconciliation + KPIs
│   └── test_smoke.py
├── docs/
│   ├── project_background.md          # Problem framing and stakeholder
│   ├── methodology.md                 # Architecture and algorithm detail
│   └── data_quality.md                # Playbook rules and reason codes
├── .env.example
├── requirements.txt
└── README.md
```

## Setup

Requires Python 3.11 and an OpenAI API key.

```bash
# Clone and enter the repo
git clone https://github.com/yashkhadilkar/SeeWeeS-Ops-Reporting-Agent.git
cd SeeWeeS-Ops-Reporting-Agent
git checkout feature/trend-analysis

# Create and activate the virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Open .env and fill in OPENAI_API_KEY. Leave Gmail/Zoho fields blank
# unless you want the report emailed.
```

## Run

```bash
python src/main.py
```

The pipeline runs end-to-end: PDF context retrieval → CSV reconciliation and trend analysis → weather fetch → planner → report. The first 2000 characters of the HTML report print to the terminal, and the full report is saved to `output_report.html`.

To view the rendered report:

```bash
open output_report.html
```

## Run the tests

```bash
python -m pytest tests/test_trend_analysis.py -v
```

Should report 25 passed.

## Key configuration

`.env` settings:

- `OPENAI_API_KEY` — required
- `WEATHER_LAT`, `WEATHER_LON`, `WEATHER_TZ` — defaults to Newark NJ (along the I-95 dispatch corridor)
- Gmail/Zoho SMTP fields — optional, leave blank to skip email delivery
- `LANGCHAIN_*` — optional LangSmith tracing

`main.py` is currently pointed at `data-for-enhancement/Incoming_shipments_14d_multi_corridor.csv` (the 14-day file with period structure). The original single-day CSV at `data/Incoming_shipment_02_08.csv` also works but won't produce period-over-period output.