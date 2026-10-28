"""Interactive metadata attached to persisted tool calls for a single turn.

This collector is transport-independent: normal completion, cancellation rescue
and replay all use the same idempotent merge operation.
"""


class InteractiveTurnMetadata:
    def __init__(self) -> None:
        self.delegation_to_toolcall: dict[str, str] = {}
        self.delegations_map: dict[str, dict] = {}
        self.confirmations_order: list[str] = []
        self.confirmations_map: dict[str, dict] = {}

    def track(self, evt: dict) -> None:
        """Track delegation events for persistence in message tool_calls."""
        evt_type = evt.get("type", "")
        del_id = evt.get("delegation_id", "")

        if evt_type == "delegation_start":
            tc_id = evt.get("tool_call_id", "")
            if tc_id:
                self.delegation_to_toolcall[del_id] = tc_id
            self.delegations_map[tc_id] = {
                "delegation_id": del_id,
                "child_agent_id": evt.get("child_agent_id", ""),
                "child_agent_name": evt.get("child_agent_name", ""),
                "child_agent_icon": evt.get("child_agent_icon", ""),
                "is_dynamic": evt.get("is_dynamic", False),
                "task": evt.get("task", ""),
                "status": "running",
                "thinking": "",
                "toolCalls": [],
                "result": "",
            }
        elif evt_type == "delegation_thinking":
            tc_id = self.delegation_to_toolcall.get(del_id, "")
            if tc_id and tc_id in self.delegations_map:
                self.delegations_map[tc_id]["thinking"] += evt.get("content", "")
        elif evt_type == "delegation_tool_call":
            tc_id = self.delegation_to_toolcall.get(del_id, "")
            if tc_id and tc_id in self.delegations_map:
                self.delegations_map[tc_id]["toolCalls"].append({
                    "id": evt.get("id", ""),
                    "name": evt.get("name", ""),
                    "arguments": evt.get("arguments", {}),
                })
        elif evt_type == "delegation_tool_result":
            tc_id = self.delegation_to_toolcall.get(del_id, "")
            if tc_id and tc_id in self.delegations_map:
                for d_tc in self.delegations_map[tc_id]["toolCalls"]:
                    if d_tc["id"] == evt.get("tool_call_id", ""):
                        d_tc["result"] = {
                            "status": evt.get("status", ""),
                            "preview": evt.get("preview", ""),
                        }
                        break
        elif evt_type == "delegation_text_delta":
            tc_id = self.delegation_to_toolcall.get(del_id, "")
            if tc_id and tc_id in self.delegations_map:
                self.delegations_map[tc_id]["result"] += evt.get("content", "")
        elif evt_type == "delegation_end":
            tc_id = self.delegation_to_toolcall.get(del_id, "")
            if tc_id and tc_id in self.delegations_map:
                self.delegations_map[tc_id]["status"] = evt.get("status", "completed")
                self.delegations_map[tc_id]["duration_ms"] = evt.get("duration_ms", 0)
                self.delegations_map[tc_id]["error"] = evt.get("error")
                # Use result from delegation_end if result is empty
                if not self.delegations_map[tc_id]["result"]:
                    self.delegations_map[tc_id]["result"] = evt.get("result_preview", "")

        elif evt_type == "confirmation_required":
            c_id = evt.get("confirmation_id", "")
            if c_id:
                self.confirmations_order.append(c_id)
                self.confirmations_map[c_id] = {
                    "confirmation_id": c_id,
                    "question": evt.get("question", ""),
                    "mode": evt.get("mode", ""),
                    "options": evt.get("options") or [],
                    "table_schema": evt.get("table_schema"),
                    "context": evt.get("context") or {},
                }
        elif evt_type == "confirmation_resolved":
            c_id = evt.get("confirmation_id", "")
            if c_id and c_id in self.confirmations_map:
                self.confirmations_map[c_id]["resolved"] = {
                    "status": evt.get("status", ""),
                    "selected_options": evt.get("selected_options") or [],
                    "user_input": evt.get("user_input"),
                    "table_data": evt.get("table_data"),
                    "resolved_at": evt.get("resolved_at"),
                }

    def merge(self, tool_calls: list[dict]) -> None:
        """Fold transport-specific delegation/confirmation data into tool calls.

        Idempotent — safe to call from the normal completion path and
        again from an interruption handler.
        """
        for tc in tool_calls:
            if tc["name"] == "delegate_task" and tc["id"] in self.delegations_map:
                tc["delegation"] = self.delegations_map[tc["id"]]
        ask_tcs = [tc for tc in tool_calls if tc["name"] == "AskUserQuestion"]
        for tc, c_id in zip(ask_tcs, self.confirmations_order, strict=False):
            tc["confirmation"] = self.confirmations_map.get(c_id)
