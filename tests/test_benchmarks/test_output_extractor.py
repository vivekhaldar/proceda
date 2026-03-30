# ABOUTME: Tests for extracting structured output from Proceda run events.
# ABOUTME: Verifies extraction from TOOL_COMPLETED events and fallback to assistant messages.

from __future__ import annotations

import json
from datetime import datetime

from benchmarks.sop_bench.output_extractor import extract_output
from proceda.events import EventType, RunEvent


def _make_event(event_type: EventType, payload: dict) -> RunEvent:
    return RunEvent(
        id="evt_test",
        timestamp=datetime.now(),
        run_id="run_test",
        type=event_type,
        payload=payload,
    )


class TestExtractFromToolResults:
    def test_extracts_single_field(self):
        events = [
            _make_event(
                EventType.TOOL_COMPLETED,
                {
                    "tool_call_id": "tc1",
                    "tool_name": "sop-bench__validateInsurance",
                    "result": json.dumps({"insurance_validation": "valid"}),
                },
            ),
        ]
        output = extract_output(events, ["insurance_validation"])
        assert output == {"insurance_validation": "valid"}

    def test_extracts_multiple_fields_from_multiple_tools(self):
        events = [
            _make_event(
                EventType.TOOL_COMPLETED,
                {
                    "tool_call_id": "tc1",
                    "tool_name": "sop-bench__validateInsurance",
                    "result": json.dumps({"insurance_validation": "valid"}),
                },
            ),
            _make_event(
                EventType.TOOL_COMPLETED,
                {
                    "tool_call_id": "tc2",
                    "tool_name": "sop-bench__verifyPharmacy",
                    "result": json.dumps({"pharmacy_check": "yes"}),
                },
            ),
            _make_event(
                EventType.TOOL_COMPLETED,
                {
                    "tool_call_id": "tc3",
                    "tool_name": "sop-bench__registerPatient",
                    "result": json.dumps({"user_registration": "success"}),
                },
            ),
        ]
        expected_cols = ["insurance_validation", "pharmacy_check", "user_registration"]
        output = extract_output(events, expected_cols)
        assert output == {
            "insurance_validation": "valid",
            "pharmacy_check": "yes",
            "user_registration": "success",
        }

    def test_ignores_non_tool_events(self):
        events = [
            _make_event(EventType.STEP_STARTED, {"step": 1}),
            _make_event(
                EventType.TOOL_COMPLETED,
                {
                    "tool_call_id": "tc1",
                    "tool_name": "sop-bench__validateInsurance",
                    "result": json.dumps({"insurance_validation": "valid"}),
                },
            ),
            _make_event(EventType.STEP_COMPLETED, {"step": 1}),
        ]
        output = extract_output(events, ["insurance_validation"])
        assert output == {"insurance_validation": "valid"}

    def test_ignores_fields_not_in_expected_columns(self):
        events = [
            _make_event(
                EventType.TOOL_COMPLETED,
                {
                    "tool_call_id": "tc1",
                    "tool_name": "sop-bench__validateInsurance",
                    "result": json.dumps(
                        {"insurance_validation": "valid", "extra_field": "ignored"}
                    ),
                },
            ),
        ]
        output = extract_output(events, ["insurance_validation"])
        assert output == {"insurance_validation": "valid"}

    def test_last_value_wins_on_duplicate_tool_calls(self):
        events = [
            _make_event(
                EventType.TOOL_COMPLETED,
                {
                    "tool_call_id": "tc1",
                    "tool_name": "sop-bench__validateInsurance",
                    "result": json.dumps({"insurance_validation": "invalid"}),
                },
            ),
            _make_event(
                EventType.TOOL_COMPLETED,
                {
                    "tool_call_id": "tc2",
                    "tool_name": "sop-bench__validateInsurance",
                    "result": json.dumps({"insurance_validation": "valid"}),
                },
            ),
        ]
        output = extract_output(events, ["insurance_validation"])
        assert output == {"insurance_validation": "valid"}


class TestFallbackToAssistantMessage:
    def test_falls_back_to_final_output_tag(self):
        result_json = json.dumps(
            {
                "insurance_validation": "valid",
                "pharmacy_check": "yes",
            }
        )
        events = [
            _make_event(
                EventType.MESSAGE_ASSISTANT,
                {
                    "content": f"Here is the result:\n<final_output>{result_json}</final_output>",
                },
            ),
        ]
        expected_cols = ["insurance_validation", "pharmacy_check"]
        output = extract_output(events, expected_cols)
        assert output == {"insurance_validation": "valid", "pharmacy_check": "yes"}

    def test_falls_back_to_llm_extraction_for_prose(self):
        """LLM extraction handles prose that deterministic strategies miss."""
        events = [
            _make_event(
                EventType.MESSAGE_ASSISTANT,
                {
                    "content": (
                        "Based on the analysis, the product has been classified as "
                        "Hazard Class C with a cumulative hazard score of 15."
                    ),
                },
            ),
        ]
        output = extract_output(events, ["hazard_class"])
        assert output.get("hazard_class") is not None
        assert "C" in output["hazard_class"] or "c" in output["hazard_class"].lower()

    def test_returns_empty_dict_when_no_data(self):
        events = [
            _make_event(EventType.RUN_COMPLETED, {"status": "completed"}),
        ]
        output = extract_output(events, ["insurance_validation"])
        assert output == {}

    def test_assistant_message_takes_priority_over_tool_results(self):
        events = [
            _make_event(
                EventType.TOOL_COMPLETED,
                {
                    "tool_call_id": "tc1",
                    "tool_name": "sop-bench__validateInsurance",
                    "result": json.dumps({"insurance_validation": "valid"}),
                },
            ),
            _make_event(
                EventType.MESSAGE_ASSISTANT,
                {
                    "content": '<final_output>{"insurance_validation": "invalid"}</final_output>',
                },
            ),
        ]
        output = extract_output(events, ["insurance_validation"])
        assert output == {"insurance_validation": "invalid"}
