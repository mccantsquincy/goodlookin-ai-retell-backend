"""Statically audit a local Retell Composer JSON export without uploading it.

python -m scripts.audit_retell_export path/to/export.json --base-url https://api.example.com
This cannot confirm a live deployment, prompt transitions, or a real calendar.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

EXPECTED_TOOLS = frozenset(
    {
        "get_business_context",
        "save_lead",
        "check_service_eligibility",
        "get_available_appointments",
        "book_appointment",
        "get_booking_status",
        "request_callback",
    }
)
BOOKING_TOOLS = frozenset(
    {
        "check_service_eligibility",
        "get_available_appointments",
        "book_appointment",
        "get_booking_status",
    }
)


def destinations(node: dict) -> set[str]:
    """Follow regular, else and skip edges; never attempt to interpret prompts."""
    result = {edge["destination_node_id"] for edge in node.get("edges", []) if edge.get("destination_node_id")}
    for key in ("else_edge", "skip_response_edge"):
        edge = node.get(key)
        if edge and edge.get("destination_node_id"):
            result.add(edge["destination_node_id"])
    return result


def audit_export(data: dict, base_url: str | None = None) -> dict:
    """Return a non-sensitive summary of graph reachability and URLs."""
    flow = data.get("conversationFlow", {})
    nodes = flow.get("nodes", [])
    tools = flow.get("tools", [])
    node_by_id = {node["id"]: node for node in nodes}
    roots = [flow.get("start_node_id")]
    roots.extend(node["id"] for node in nodes if node.get("global_node_setting"))
    visited: set[str] = set()
    pending = [node_id for node_id in roots if node_id]
    dangling: set[str] = set()
    while pending:
        node_id = pending.pop()
        if node_id in visited:
            continue
        if node_id not in node_by_id:
            dangling.add(node_id)
            continue
        visited.add(node_id)
        pending.extend(destinations(node_by_id[node_id]) - visited)

    tool_ids = set()
    for node_id in visited:
        node = node_by_id[node_id]
        if node.get("tool_id"):
            tool_ids.add(node["tool_id"])
        tool_ids.update(node.get("tool_ids", []))
    tool_by_name = {tool["name"]: tool for tool in tools}
    reachable_tools = sorted(name for name, tool in tool_by_name.items() if tool.get("tool_id") in tool_ids)

    url_issues = []
    expected_base = base_url.rstrip("/") if base_url else None
    for name, tool in tool_by_name.items():
        url = tool.get("url", "")
        parsed = urlsplit(url)
        required_path = f"/v1/retell/tools/{name}"
        if parsed.scheme != "https" or not parsed.netloc or parsed.path != required_path:
            url_issues.append(f"{name}: invalid HTTPS URL or endpoint path")
        if expected_base and not url.startswith(f"{expected_base}/v1/retell/tools/"):
            url_issues.append(f"{name}: URL differs from expected backend")
    webhook = data.get("webhook_url", "")
    parsed_hook = urlsplit(webhook)
    if parsed_hook.scheme != "https" or parsed_hook.path != "/v1/retell/webhooks":
        url_issues.append("webhook: invalid HTTPS URL or endpoint path")
    if expected_base and webhook != f"{expected_base}/v1/retell/webhooks":
        url_issues.append("webhook: URL differs from expected backend")
    for node in nodes:
        dangling.update(destinations(node) - set(node_by_id))

    return {
        "agent_name": data.get("agent_name", "(unnamed)"),
        "version": data.get("version"),
        "published_in_export": data.get("is_published") is True,
        "node_count": len(nodes),
        "tool_count": len(tools),
        "reachable_nodes": sorted(visited),
        "unreachable_nodes": sorted(set(node_by_id) - visited),
        "reachable_tools": reachable_tools,
        "unreachable_tools": sorted(set(tool_by_name) - set(reachable_tools)),
        "booking_path_reachable": BOOKING_TOOLS.issubset(reachable_tools),
        "missing_tool_definitions": sorted(EXPECTED_TOOLS - set(tool_by_name)),
        "dangling_node_destinations": sorted(dangling),
        "url_issues": sorted(url_issues),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="Path to a local private Retell Composer export")
    parser.add_argument("--base-url", help="Expected public HTTPS backend origin")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON summary")
    parser.add_argument("--require-booking", action="store_true", help="Fail if booking graph cannot be reached")
    args = parser.parse_args()
    try:
        report = audit_export(json.loads(args.export.read_text(encoding="utf-8")), args.base_url)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.error(f"cannot inspect export: {exc}")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Agent: {report['agent_name']} (export v{report['version']})")
        print(f"Published in export: {report['published_in_export']}")
        print(f"Nodes: {report['node_count']} | Tools: {report['tool_count']}")
        print("Reachable tools:", ", ".join(report["reachable_tools"]) or "none")
        print("Unreachable nodes:", ", ".join(report["unreachable_nodes"]) or "none")
        print("Unreachable tools:", ", ".join(report["unreachable_tools"]) or "none")
        print("Booking route reachable:", report["booking_path_reachable"])
        for key in ("missing_tool_definitions", "dangling_node_destinations", "url_issues"):
            if report[key]:
                print(f"Review {key}:", "; ".join(report[key]))
        print("Static review only: no live Retell or backend call is made.")
    problems = report["missing_tool_definitions"] or report["dangling_node_destinations"] or report["url_issues"]
    return 1 if problems or (args.require_booking and not report["booking_path_reachable"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
