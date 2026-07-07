#!/usr/bin/env python3
"""
Trend Monitoring Agent
----------------------
Runs on a schedule (see .github/workflows/trend-agent.yml). For every indicator
in roster.json, asks Claude (with web search) to find the latest value from the
named source, classify it against the pre-defined threshold bands, and return a
structured result. Writes/updates data.json, which the dashboard.html artifact
reads on load.

Requires: ANTHROPIC_API_KEY environment variable.
Install:  pip install anthropic
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

import anthropic

MODEL = "claude-sonnet-5"
ROSTER_PATH = "roster.json"
DATA_PATH = "data.json"

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env


def build_prompt(indicator: dict) -> str:
    thresholds_desc = "\n".join(
        f"- {t['label']}: {t['band']}" for t in indicator["thresholds"]
    )
    return f"""You are checking one labor/education/technology market indicator for a
monitoring dashboard. Use web search to find the most recent published value.

Indicator: {indicator['name']}
Source to check: {indicator['source']}
Suggested search: {indicator['source_query']}
What we're tracking: {indicator['description']}

Classify the latest value against these threshold bands:
{thresholds_desc}

Respond with ONLY a JSON object, no other text, no markdown fences:
{{
  "status_label": "<one of the threshold labels above, exactly as written>",
  "value": "<the latest figure or short description of what you found>",
  "as_of": "<date or period the data covers, e.g. 'June 2026' or 'Q2 2026'>",
  "note": "<one sentence explaining the classification, plain language>",
  "source_url": "<the URL of the source you used>"
}}

If you cannot find a recent enough figure to confidently classify, set status_label
to "No update" and explain why in note."""


def query_indicator(indicator: dict) -> dict:
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": build_prompt(indicator)}],
        )
        text_blocks = [b.text for b in response.content if b.type == "text"]
        raw = "\n".join(text_blocks).strip()
        raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        parsed = json.loads(match.group(0)) if match else {}
        return {
            "status_label": parsed.get("status_label", "No update"),
            "value": parsed.get("value", ""),
            "as_of": parsed.get("as_of", ""),
            "note": parsed.get("note", ""),
            "source_url": parsed.get("source_url", ""),
        }
    except Exception as exc:  # noqa: BLE001 - log and continue, don't kill the whole run
        print(f"  [error] {indicator['id']}: {exc}", file=sys.stderr)
        return {
            "status_label": "No update",
            "value": "",
            "as_of": "",
            "note": f"Agent error: {exc}",
            "source_url": "",
        }


def load_previous() -> dict:
    if os.path.exists(DATA_PATH):
        with open(DATA_PATH, "r") as f:
            return json.load(f)
    return {}


def main():
    with open(ROSTER_PATH, "r") as f:
        roster = json.load(f)

    previous = load_previous()
    prev_history = {}
    for domain in previous.get("domains", []):
        for trend in domain.get("trends", []):
            for ind in trend.get("indicators", []):
                prev_history[ind["id"]] = ind.get("history", [])

    now = datetime.now(timezone.utc).isoformat()
    out = {"generated_at": now, "domains": []}

    for domain in roster["domains"]:
        out_domain = {"name": domain["name"], "trends": []}
        for trend in domain["trends"]:
            out_trend = {"id": trend["id"], "title": trend["title"], "indicators": []}
            for indicator in trend["indicators"]:
                print(f"Checking: {indicator['name']} ...")
                result = query_indicator(indicator)

                history = prev_history.get(indicator["id"], [])
                history.append({
                    "checked_at": now,
                    "status_label": result["status_label"],
                    "value": result["value"],
                    "as_of": result["as_of"],
                })
                history = history[-12:]  # keep last 12 checks

                out_indicator = {**indicator, "current": result, "history": history}
                out_trend["indicators"].append(out_indicator)
            out_domain["trends"].append(out_trend)
        out["domains"].append(out_domain)

    with open(DATA_PATH, "w") as f:
        json.dump(out, f, indent=2)

    print(f"\nWrote {DATA_PATH} at {now}")


if __name__ == "__main__":
    main()
