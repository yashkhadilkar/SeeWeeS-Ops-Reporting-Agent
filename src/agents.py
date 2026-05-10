from __future__ import annotations
from typing import Dict, Any
from langchain_openai import ChatOpenAI
from prompts import PDF_CONTEXT_PROMPT, OPS_ANALYSIS_PROMPT, PLANNER_PROMPT, REPORT_PROMPT

llm = ChatOpenAI(
    model="gpt-4.1-mini",
    temperature=0.2,
    tags=["msba-demo", "multi-agent"],
    metadata={"repo": "MSBA_AI_Agents_Demo"}
)


def run_context_agent(snippets: str) -> str:
    return llm.invoke(PDF_CONTEXT_PROMPT.format_messages(snippets=snippets)).content

def run_ops_agent(
    summary: Dict[str, Any],
    kpis: Dict[str, Any],
    anomalies_md: str,
    reconciliation_summary: Dict[str, Any] | None = None,
    period_kpis: Dict[str, Any] | None = None,
) -> str:
    return llm.invoke(OPS_ANALYSIS_PROMPT.format_messages(
        summary=summary,
        kpis=kpis,
        anomalies_md=anomalies_md,
        reconciliation_summary=reconciliation_summary or {},
        period_kpis=period_kpis or {},
    )).content


def run_planner_agent(
    business_context: str,
    ops_insights: str,
    weather_risk: Dict[str, Any],
    reconciliation_summary: Dict[str, Any] | None = None,
    period_kpis: Dict[str, Any] | None = None,
) -> str:
    return llm.invoke(PLANNER_PROMPT.format_messages(
        business_context=business_context,
        ops_insights=ops_insights,
        weather_risk=weather_risk,
        reconciliation_summary=reconciliation_summary or {},
        period_kpis=period_kpis or {},
    )).content


def run_report_agent(
    business_context: str,
    kpis: Dict[str, Any],
    anomaly_highlights: str,
    weather_risk: Dict[str, Any],
    dispatch_plan: str,
    reconciliation_summary: Dict[str, Any] | None = None,
    period_kpis: Dict[str, Any] | None = None,
) -> str:
    return llm.invoke(REPORT_PROMPT.format_messages(
        business_context=business_context,
        kpis=kpis,
        anomaly_highlights=anomaly_highlights,
        weather_risk=weather_risk,
        dispatch_plan=dispatch_plan,
        reconciliation_summary=reconciliation_summary or {},
        period_kpis=period_kpis or {},
    )).content