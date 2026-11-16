from copy import deepcopy

import pytest
from ree.checkers import check_tools
from ree.conversation import case_from_messages


def declaration(name="read_config"):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "Read an explicitly named configuration.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    }


def call(identifier="transport-a", name="read_config", arguments='{"path":"config.json"}'):
    return {
        "id": identifier,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def recorded_turn():
    return {
        "messages": [
            {"role": "system", "content": "Use supplied tools and report observed facts."},
            {"role": "user", "content": "Read the configuration."},
            {
                "role": "assistant",
                "content": "Checking the file.",
                "tools": [declaration()],
                "tool_calls": [call()],
            },
            {"role": "tool", "tool_call_id": "transport-a", "content": '{  "enabled" : true }\n'},
            {
                "role": "assistant",
                "content": "The setting is enabled.",
                "reasoning_content": "Private scratch work.",
            },
        ]
    }


def test_normalizes_observed_messages_and_ignores_external_labels_and_metadata():
    source = recorded_turn()
    source.update(
        legacy_rubric="unrelated",
        rubric="unrelated",
        post_labels=["unrelated"],
        user_id="unused",
        lat=123,
    )
    before = deepcopy(source)
    case = case_from_messages(source)
    assert source == before
    assert case.request == "Read the configuration."
    assert case.response == "The setting is enabled."
    assert case.harness.system_prompt == source["messages"][0]["content"]
    assert case.subjects is None and case.context is None and case.history == []
    assert "Private scratch work" not in case.model_dump_json()
    assert "unrelated" not in case.model_dump_json() and "unused" not in case.model_dump_json()
    assert case.evidence_complete is True
    calls = [event for event in case.events if event.kind == "tool_call"]
    results = [event for event in case.events if event.kind == "tool_result"]
    assert calls[0].ref == "msg-3-call-1" and results[0].ref == "result-4"
    assert results[0].call_ref == calls[0].ref
    assert results[0].output == '{  "enabled" : true }\n'
    assert results[0].error is None
    assert calls[0].arguments == {"path": "config.json"}
    assert all(event.harness is None for event in case.events)
    assert check_tools(case).value is True


def test_dynamic_tools_are_not_unioned_and_results_keep_their_call_snapshot():
    source = recorded_turn()
    source["messages"].insert(
        4,
        {
            "role": "assistant",
            "content": "",
            "tools": [declaration("read_status")],
            "tool_calls": [call("transport-b", "read_status")],
        },
    )
    source["messages"].insert(
        5, {"role": "tool", "tool_call_id": "transport-b", "content": "Ready"}
    )
    case = case_from_messages(source)
    assert [tool.name for tool in case.harness.tools] == ["read_config"]
    calls = [event for event in case.events if event.kind == "tool_call"]
    results = [event for event in case.events if event.kind == "tool_result"]
    assert calls[0].harness is None
    assert [tool.name for tool in calls[1].harness.tools] == ["read_status"]
    assert results[0].harness is None
    assert [tool.name for tool in results[1].harness.tools] == ["read_status"]
    assert results[1].call_ref == calls[1].ref
    assert check_tools(case).value is True


def test_late_schemas_are_not_visible_to_the_initial_assistant():
    source = recorded_turn()
    source["messages"].insert(
        2, {"role": "assistant", "content": "I will inspect the configuration."}
    )
    case = case_from_messages(source)
    assert case.harness.tools == []
    event = next(event for event in case.events if event.kind == "tool_call")
    assert [tool.name for tool in event.harness.tools] == ["read_config"]
    assert event.ref == "msg-4-call-1"
    assert check_tools(case).value is True


def test_explicit_empty_tool_snapshot_revokes_available_tools():
    source = recorded_turn()
    source["messages"].insert(
        4, {"role": "assistant", "content": "", "tools": [], "tool_calls": [call("transport-b")]}
    )
    source["messages"].insert(
        5, {"role": "tool", "tool_call_id": "transport-b", "content": "Observed result"}
    )
    case = case_from_messages(source)
    second = [event for event in case.events if event.kind == "tool_call"][1]
    assert second.harness is not None and second.harness.tools == []
    # An explicit empty snapshot never falls back to the initial declarations.
    assert check_tools(case).status == "missing_evidence"


def test_parallel_calls_associate_by_transport_id_without_subject_ids():
    source = recorded_turn()
    source["messages"][2]["tool_calls"].append(call("transport-b"))
    source["messages"].insert(
        3,
        {
            "role": "tool",
            "tool_call_id": "transport-b",
            "name": "read_config",
            "content": "Second result",
        },
    )
    case = case_from_messages(source)
    results = [event for event in case.events if event.kind == "tool_result"]
    assert results[0].call_ref == "msg-3-call-2"
    assert results[1].call_ref == "msg-3-call-1"
    assert case.subjects is None
    assert "transport-a" not in case.model_dump_json()


def test_late_tool_return_uses_associated_call_harness_not_the_latest_snapshot():
    source = recorded_turn()
    original_result = source["messages"].pop(3)
    source["messages"].insert(
        3,
        {
            "role": "assistant",
            "content": "",
            "tools": [declaration("read_status")],
            "tool_calls": [call("transport-b", "read_status")],
        },
    )
    source["messages"].insert(4, original_result)
    source["messages"].insert(
        5, {"role": "tool", "tool_call_id": "transport-b", "content": "Second result"}
    )
    case = case_from_messages(source)
    results = [event for event in case.events if event.kind == "tool_result"]
    assert results[0].harness is None
    assert [tool.name for tool in results[1].harness.tools] == ["read_status"]


@pytest.mark.parametrize(
    "arguments",
    ['{"broken":', "[]", "null", '{"path":1,"path":2}', '{"path":NaN}', '{"path":1e309}', False],
)
def test_invalid_arguments_fail_explicitly_without_replacing_them(arguments):
    source = recorded_turn()
    source["messages"][2]["tool_calls"][0]["function"]["arguments"] = arguments
    with pytest.raises(ValueError, match="Message 3, call 1: invalid function arguments"):
        case_from_messages(source)


def test_tool_content_does_not_guess_execution_errors_from_text_or_extra_metadata():
    source = recorded_turn()
    source["messages"][3].update(
        content='{"status":"error","message":"not applied"}', _execution_metadata={"success": False}
    )
    result = next(
        event for event in case_from_messages(source).events if event.kind == "tool_result"
    )
    assert result.output == source["messages"][3]["content"]
    assert result.error is None


def test_missing_result_marks_incomplete_even_when_the_assistant_replies():
    source = recorded_turn()
    source["messages"].pop(3)
    case = case_from_messages(source)
    assert case.evidence_complete is False
    assert case.response == "The setting is enabled."


def test_terminal_tool_result_is_not_promoted_to_an_assistant_response():
    source = recorded_turn()
    source["messages"].pop()
    case = case_from_messages(source)
    assert case.response == ""
    assert case.evidence_complete is False


@pytest.mark.parametrize("change", ["orphan", "duplicate", "name_mismatch", "forward_result"])
def test_tool_results_reject_ambiguous_or_unobserved_association(change):
    source = recorded_turn()
    if change == "orphan":
        source["messages"][3]["tool_call_id"] = "unknown"
    elif change == "duplicate":
        source["messages"].insert(4, deepcopy(source["messages"][3]))
    elif change == "name_mismatch":
        source["messages"][3]["name"] = "another_tool"
    else:
        source["messages"].insert(2, source["messages"].pop(3))
    with pytest.raises(ValueError, match="tool result|preceding assistant call"):
        case_from_messages(source)


def test_multiple_user_turns_fail_instead_of_silently_dropping_history():
    source = recorded_turn()
    source["messages"].append({"role": "user", "content": "A follow-up request."})
    with pytest.raises(ValueError, match="found 2 user messages"):
        case_from_messages(source)


def test_system_parts_preserve_initial_and_later_visibility():
    source = recorded_turn()
    source["messages"].insert(1, {"role": "system", "content": "Second system instruction."})
    source["messages"].insert(5, {"role": "system", "content": "Later instruction."})
    case = case_from_messages(source)
    assert (
        case.harness.system_prompt
        == "Use supplied tools and report observed facts.\n\nSecond system instruction."
    )
    context = next(event for event in case.events if event.kind == "context")
    assert context.content == "Later instruction."
    assert context.harness.system_prompt.endswith("\n\nLater instruction.")


def test_plain_response_accepts_nullable_call_fields_but_excludes_reasoning():
    case = case_from_messages(
        {
            "messages": [
                {"role": "user", "content": "Say hello."},
                {
                    "role": "assistant",
                    "content": "Hello",
                    "tool_calls": None,
                    "reasoning_content": "Draft thinking",
                },
            ]
        }
    )
    assert case.response == "Hello" and case.events == []
    assert case.evidence_complete is True


def test_multimodal_messages_are_explicitly_unsupported():
    with pytest.raises(ValueError, match="multimodal"):
        case_from_messages(
            {
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
                    {"role": "assistant", "content": "Hello"},
                ]
            }
        )
