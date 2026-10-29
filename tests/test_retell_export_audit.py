"""Synthetic examples: no real agent exports or credentials in GitHub."""

from scripts.audit_retell_export import audit_export

BASE = "https://api.example.test"


def make_export():
    return {
        "agent_name": "Fictional Agent",
        "version": 2,
        "is_published": False,
        "webhook_url": BASE + "/v1/retell/webhooks",
        "conversationFlow": {
            "start_node_id": "start",
            "nodes": [
                {
                    "id": "start",
                    "type": "function",
                    "tool_id": "context",
                    "edges": [{"destination_node_id": "intake"}],
                },
                {
                    "id": "intake",
                    "type": "subagent",
                    "tool_ids": ["lead"],
                    "else_edge": {"destination_node_id": "end"},
                },
                {"id": "end", "type": "end"},
                {"id": "booking", "type": "function", "tool_id": "book"},
                {
                    "id": "safety",
                    "type": "conversation",
                    "global_node_setting": {"condition": {"type": "prompt"}},
                    "skip_response_edge": {"destination_node_id": "end"},
                },
            ],
            "tools": [
                {
                    "name": "get_business_context",
                    "tool_id": "context",
                    "url": BASE + "/v1/retell/tools/get_business_context",
                },
                {"name": "save_lead", "tool_id": "lead", "url": BASE + "/v1/retell/tools/save_lead"},
                {"name": "book_appointment", "tool_id": "book", "url": BASE + "/v1/retell/tools/book_appointment"},
            ],
        },
    }


def test_reports_disconnected_booking_and_counts_global_safety():
    result = audit_export(make_export(), BASE)
    assert result["unreachable_nodes"] == ["booking"]
    assert result["unreachable_tools"] == ["book_appointment"]
    assert "safety" in result["reachable_nodes"]
    assert not result["booking_path_reachable"]
    assert not result["url_issues"]


def test_traverses_new_link_to_booking():
    data = make_export()
    data["conversationFlow"]["nodes"][1]["edges"] = [{"destination_node_id": "booking"}]
    result = audit_export(data)
    assert "booking" in result["reachable_nodes"]
    assert "book_appointment" in result["reachable_tools"]
    assert not result["unreachable_nodes"]


def test_reports_dangling_edges_and_wrong_url():
    data = make_export()
    data["conversationFlow"]["nodes"][0]["edges"].append({"destination_node_id": "missing"})
    data["conversationFlow"]["tools"][0]["url"] = "http://example.test/wrong"
    result = audit_export(data, BASE)
    assert result["dangling_node_destinations"] == ["missing"]
    assert any("get_business_context" in issue for issue in result["url_issues"])


def test_export_publication_flag_does_not_mean_live():
    data = make_export()
    data["is_published"] = True
    result = audit_export(data)
    assert result["published_in_export"] is True
    assert "live" not in result
