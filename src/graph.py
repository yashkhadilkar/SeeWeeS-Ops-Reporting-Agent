from __future__ import annotations
import os
from typing import TypedDict, Dict, Any

from langgraph.graph import StateGraph, END
from dotenv import load_dotenv

from tools.pdf_tools import PdfRag
from tools.csv_tools import analyze_csv
from tools.weather_tools import get_weather_forecast, derive_dispatch_weather_risk
from tools.email_tools import send_email_smtp
from agents import run_context_agent, run_ops_agent, run_planner_agent, run_report_agent

load_dotenv()

class AppState(TypedDict, total=False):
    pdf_path: str
    csv_path: str

    business_context: str

    csv_summary: Dict[str, Any]
    csv_kpis: Dict[str, Any]
    anomalies_md: str
    ops_insights: str

    # New for Option 3 (Deep-Dive Trend Analysis)
    reconciliation_summary: Dict[str, Any]
    period_kpis: Dict[str, Any]

    weather_forecast: Dict[str, Any]
    weather_risk: Dict[str, Any]

    dispatch_plan: str
    report_html: str


def node_pdf_context(state: AppState) -> AppState:
    rag = PdfRag(persist_dir="chroma_db")
    vectordb = rag.build(state["pdf_path"])
    retriever = rag.retriever(vectordb, k=6)

    query = "Extract KPI definitions, thresholds, SLAs, constraints, dispatch rules, exceptions."
    docs = retriever.invoke(query)
    snippets = "\n\n---\n\n".join(d.page_content for d in docs)

    business_context = run_context_agent(snippets)
    return {"business_context": business_context}


def node_csv_analysis(state: AppState) -> AppState:
    res = analyze_csv(state["csv_path"])

    anomalies_md = "(none detected or insufficient numeric data)"
    if not res.anomalies.empty:
        # Show a compact view of the anomalies with the canonical columns we added.
        cols_priority = [
            "shipment_date", "corridor_id", "item_id", "item_name",
            "canonical_item_id", "reason_code", "is_dispatchable",
            "anomaly_score",
        ]
        cols_present = [c for c in cols_priority if c in res.anomalies.columns]
        anomalies_md = res.anomalies[cols_present].head(12).to_markdown(index=False)

    ops_insights = run_ops_agent(
        summary=res.summary,
        kpis=res.kpis,
        anomalies_md=anomalies_md,
        reconciliation_summary=res.reconciliation_summary,
        period_kpis=res.period_kpis,
    )

    return {
        "csv_summary": res.summary,
        "csv_kpis": res.kpis,
        "anomalies_md": anomalies_md,
        "ops_insights": ops_insights,
        "reconciliation_summary": res.reconciliation_summary,
        "period_kpis": res.period_kpis,
    }


def node_weather(state: AppState) -> AppState:
    lat = os.getenv("WEATHER_LAT", "40.7282")
    lon = os.getenv("WEATHER_LON", "-74.0776")
    tz = os.getenv("WEATHER_TZ", "America/New_York")

    forecast = get_weather_forecast(lat, lon, tz)
    risk = derive_dispatch_weather_risk(forecast)
    return {"weather_forecast": forecast, "weather_risk": risk}


def node_planner(state: AppState) -> AppState:
    plan = run_planner_agent(
        business_context=state.get("business_context", ""),
        ops_insights=state.get("ops_insights", ""),
        weather_risk=state.get("weather_risk", {}),
        reconciliation_summary=state.get("reconciliation_summary", {}),
        period_kpis=state.get("period_kpis", {}),
    )
    return {"dispatch_plan": plan}


def node_report(state: AppState) -> AppState:
    html = run_report_agent(
        business_context=state.get("business_context", ""),
        kpis=state.get("csv_kpis", {}),
        anomaly_highlights=state.get("anomalies_md", "(none)"),
        weather_risk=state.get("weather_risk", {}),
        dispatch_plan=state.get("dispatch_plan", ""),
        reconciliation_summary=state.get("reconciliation_summary", {}),
        period_kpis=state.get("period_kpis", {}),
    )
    return {"report_html": html}

def node_email(state: AppState) -> AppState:
    to_email = os.getenv("REPORT_EMAIL_TO", "").strip()
    if not to_email:
        print("REPORT_EMAIL_TO not set -> skipping email send.")
        return {}

    subject = "MSBA Ops Multi-Agent Dispatch Report"
    send_email_smtp(subject=subject, html_body=state["report_html"], to_email=to_email)
    return {}



def build_graph():
    g = StateGraph(AppState)

    g.add_node("pdf_context", node_pdf_context)
    g.add_node("csv_analysis", node_csv_analysis)
    g.add_node("weather", node_weather)
    g.add_node("planner", node_planner)
    g.add_node("report", node_report)
    g.add_node("email", node_email)

    g.set_entry_point("pdf_context")
    g.add_edge("pdf_context", "csv_analysis")
    g.add_edge("csv_analysis", "weather")
    g.add_edge("weather", "planner")
    g.add_edge("planner", "report")
    g.add_edge("report", "email")
    g.add_edge("email", END)

    return g.compile()
