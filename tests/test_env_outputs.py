from __future__ import annotations

import json

import pytest
from ree.env_outputs import build_task_pack, load_task_context, normalize_trajectory
from ree.models import TaskContext
from ree.pack import load_pack


def message(role, content, **extra):
    return {"role": role, "content": content, **extra}


def tool_call(identifier="c1"):
    return message("assistant", "", tool_calls=[{"id": identifier, "type": "function",
        "function": {"name": "update", "arguments": '{"value":2}'}}])


def tool_result(identifier="c1"):
    return message("tool", '{"ok":true}', tool_call_id=identifier, name="update")


def context():
    return TaskContext(task_description="Update the selected value")


def test_cumulative_snapshots_do_not_duplicate_calls_or_messages():
    first = [message("user", "Update the selected value"), tool_call()]
    second = first + [tool_result(), message("assistant", "Updated.")]
    case = normalize_trajectory({"call_list": [{"messages": first}, {"messages": second}]}, context())
    assert len([e for e in case.events if e.kind == "tool_call"]) == 1
    results = [e for e in case.events if e.kind == "tool_result"]
    calls = [e for e in case.events if e.kind == "tool_call"]
    assert results[0].call_ref == calls[0].ref
    assert case.response == "Updated."
    assert not case.artifacts
    assert case.evidence_complete


def test_compression_preserves_old_observable_evidence():
    first = [message("system", "Policy"), message("user", "Update"), tool_call(), tool_result()]
    compressed = [message("system", "Policy"), message("user", "Summary", isMeta=True),
                  tool_result(), message("assistant", "Updated.")]
    case = normalize_trajectory({"call_list": [{"messages": first},
        {"messages": compressed, "compressed": True}]}, context())
    assert len([e for e in case.events if e.kind == "tool_call"]) == 1
    assert len([e for e in case.events if e.kind == "tool_result"]) == 1
    assert any(e.content == "Summary" for e in case.events if e.kind == "context")
    assert case.response == "Updated."


def test_legitimate_repeated_messages_not_globally_deduplicated():
    first = [message("user", "Again"), message("assistant", "Done")]
    second = first + [message("user", "Again"), message("assistant", "Done")]
    case = normalize_trajectory({"call_list": [first, second]}, context())
    events = [e for e in case.events if e.kind == "message"]
    assert [e.content for e in events] == ["Again", "Done", "Again", "Done"]


def test_anthropic_blocks_without_hidden_thinking():
    first = [message("user", "Update"), message("assistant", [
        {"type": "thinking", "thinking": "secret hidden reasoning"},
        {"type": "tool_use", "id": "c1", "name": "update", "input": {"value": 2}},
    ])]
    second = first + [message("user", [{"type": "tool_result", "tool_use_id": "c1",
        "content": [{"type": "text", "text": "ok"}]}]), message("assistant", "Updated")]
    case = normalize_trajectory({"call_list": [first, second]}, context())
    assert len([e for e in case.events if e.kind == "tool_call"]) == 1
    assert len([e for e in case.events if e.kind == "tool_result"]) == 1
    assert "hidden reasoning" not in case.model_dump_json()


def test_unmatched_tool_result_rejected():
    with pytest.raises(ValueError, match="corresponding call"):
        normalize_trajectory({"messages": [tool_result()]}, context())


def test_missing_state_not_synthesized_from_success_claim():
    case = normalize_trajectory({"messages": [message("user", "Update"),
        message("assistant", "Successfully updated the value to 2")]}, context())
    assert case.artifacts == []
    assert '"final_state_supplied": false' in case.context.references[-1]
    case = normalize_trajectory({"messages": [message("assistant", "Updated")],
        "extra_info": {"final_state": {"value": 2}}}, context(), initial_state={"value": 1})
    assert {a.name for a in case.artifacts} == {"initial_state", "final_state"}


def test_unknown_compression_gap_marks_incomplete():
    case = normalize_trajectory({"call_list": [[message("user", "first")],
        [message("user", "unmatched second")]]}, context())
    assert not case.evidence_complete


def test_tool_output_in_snapshot_is_preserved_once():
    user = message("user", "Update")
    output = {"content": "Done", "reasoning_content": "hidden", "tool_calls": None}
    case = normalize_trajectory({"call_list": [{"messages": [user], "output": output}]}, context())
    assert case.response == "Done"
    assert "hidden" not in case.model_dump_json()


def test_task_loading_preserves_time_topology_and_sanitizes_credentials(tmp_path):
    env = tmp_path / "env"
    (env / "tools").mkdir(parents=True)
    (env / "prompts").mkdir()
    (env / "sets").mkdir()
    (env / "tools/tool_schema.json").write_text(json.dumps([{"type": "function", "function": {
        "name": "update", "description": "Updates a value", "parameters": {"type": "object"}}}]))
    (env / "prompts/system.txt").write_text("Follow the declared tool contracts.")
    (env / "harness_combo.json").write_text(json.dumps({"compact": {"trigger": 100},
        "api_key": "do-not-copy", "max_rounds": 4}))
    task = env / "sets/task.json"
    task.write_text(json.dumps({"task_instruction": "Update a value", "goal": "Value is 2",
        "tools": ["update"], "checklist": ["Value is 2"], "checklist_deps": [{"index": 0,
        "depends_on": []}], "current_datetime": "2026-04-21 13:47", "current_date": "2026-04-21",
        "rubric": {"底线项": ["Value is 2"], "加分项": ["Explain the change"],
        "条件拆解": ["Do not change unrelated values"]}, "api_key": "do-not-copy"}))
    loaded = load_task_context(task, env)
    assert loaded.success_criteria == ["Value is 2"]
    assert "2026-04-21 13:47" in "\n".join(loaded.references)
    assert "do-not-copy" not in loaded.model_dump_json()
    assert any("optional edges are optional" in r for r in loaded.references)


def test_build_task_pack_adds_state_grounding_with_context(tmp_path):
    output = tmp_path / "frozen"
    record = build_task_pack(context(), output)
    pack = load_pack(output)
    state = next(c for c in pack.manifest.criteria if c.name == "environment.state_grounding")
    assert state.group == "hallucination"
    assert state.checker == "llm"
    assert "missing_evidence" in pack.prompt(state.prompt)
    assert "initial_state" in pack.prompt(state.prompt)
    assert record["validation"]["semantic_calibration"] == "not_run"
    assert pack.context().task_description == context().task_description


def test_nonmeta_prefix_new_user_is_retained_and_marks_ambiguous_overlap():
    raw = {"call_list": [[message("user", "Original"), message("assistant", "Done")],
                         [message("user", "New"), message("assistant", "Done"),
                          message("assistant", "Final")]]}
    case = normalize_trajectory(raw, context())
    assert any(e.content == "New" for e in case.events)
    assert not case.evidence_complete


def test_tool_schema_credential_properties_are_preserved():
    schema = {"type": "function", "function": {"name": "update", "parameters": {
        "type": "object", "required": ["password"], "properties": {"password": {"type": "string"}}}}}
    raw = {"tools_schema": [schema], "messages": [message("user", "Update"),
        message("assistant", "", tool_calls=[{"id": "c1", "function": {"name": "update",
        "arguments": '{"password":"actual-secret"}'}}]), tool_result(), message("assistant", "Done")]}
    case = normalize_trajectory(raw, context())
    call = next(e for e in case.events if e.kind == "tool_call")
    assert call.harness.tools[0].parameters["properties"]["password"]["type"] == "string"
    assert call.arguments["password"] == "[redacted]"
    assert "actual-secret" not in case.model_dump_json()


def test_no_final_reply_does_not_promote_tool_call_preface():
    raw = {"messages": [message("user", "Update"), message("assistant", "I will update it", tool_calls=[{
        "id": "c1", "function": {"name": "update", "arguments": '{}'}}])]}
    case = normalize_trajectory(raw, context())
    assert case.response == ""
    assert not case.evidence_complete


def test_per_call_harness_preserves_dynamic_tool_schema():
    def declaration(kind):
        return {"type": "function", "function": {"name": "update", "parameters": {
            "type": "object", "properties": {"value": {"type": kind}}, "required": ["value"]}}}
    first = [message("user", "Update"), tool_call("c1"), tool_result("c1")]
    second = first + [message("assistant", "", tool_calls=[{"id": "c2", "function": {
        "name": "update", "arguments": '{"value":"two"}'}}]), tool_result("c2"), message("assistant", "Done")]
    case = normalize_trajectory({"call_list": [{"messages": first, "tools": [declaration("integer")]},
        {"messages": second, "tools": [declaration("string")]}]}, context())
    calls = [e for e in case.events if e.kind == "tool_call"]
    assert calls[0].harness.tools[0].parameters["properties"]["value"]["type"] == "integer"
    assert calls[1].harness.tools[0].parameters["properties"]["value"]["type"] == "string"
    from ree.checkers import check_tools
    assert check_tools(case).value is True


def test_designer_eight_increments_rejected_compositionally(tmp_path):
    from ree.adaptation import AdaptationError, Coverage, Increment, Overlay
    class TooMany:
        def judge(self, name, prompt, payload, schema):
            increments = [Increment(name=f"local.check{i}", group="format", description="local condition",
                prompt="Return evidence-based JSON judgement with missing_evidence and positive/negative examples.")
                for i in range(8)]
            return Overlay(coverage=[Coverage(requirement=r, covered_by=[increments[0].name], action="increment")
                for r in payload["requirements"]], increments=increments)
    with pytest.raises(AdaptationError, match="seven"):
        build_task_pack(context(), tmp_path / "failed", client=TooMany())
    assert not (tmp_path / "failed").exists()


def test_separate_output_omitted_from_next_input_is_retained_without_replaying_calls():
    first = [message("user", "Update"), tool_call(), tool_result()]
    raw = {"call_list": [{"messages": first, "output": {"content": "Updated"}},
        {"messages": first + [message("user", "Thanks"), message("assistant", "Welcome")]}]}
    case = normalize_trajectory(raw, context())
    assert len([e for e in case.events if e.kind == "tool_call"]) == 1
    assert any(e.content == "Updated" for e in case.events)
    assert any(e.content == "Thanks" for e in case.events)
    assert case.response == "Welcome"


def test_many_successive_inputs_omit_old_separate_reply_without_duplicate_calls():
    first = [message("user", "Update"), tool_call(), tool_result()]
    second = first + [message("user", "Again"), tool_call("c2"), tool_result("c2")]
    third = second + [message("user", "Thanks"), message("assistant", "Welcome")]
    raw = {"call_list": [{"messages": first, "output": {"content": "Updated"}},
        {"messages": second, "output": {"content": "Updated again"}}, {"messages": third}]}
    case = normalize_trajectory(raw, context())
    assert len([e for e in case.events if e.kind == "tool_call"]) == 2
    assert any(e.content == "Updated" for e in case.events)
    assert any(e.content == "Updated again" for e in case.events)
    assert case.response == "Welcome"


def test_compression_replaces_old_tool_text_without_new_operation():
    first = [message("user", "Update"), tool_call(), tool_result()]
    compressed = [message("user", "Update"), tool_call(),
        message("tool", "[old result compressed]", tool_call_id="c1", name="update"),
        message("assistant", "Done")]
    case = normalize_trajectory({"call_list": [{"messages": first},
        {"messages": compressed, "compressed": True}]}, context())
    calls = [e for e in case.events if e.kind == "tool_call"]
    results = [e for e in case.events if e.kind == "tool_result"]
    assert len(calls) == len(results) == 1
    assert results[0].output == '{"ok":true}'
    assert case.evidence_complete


def test_compression_drops_old_middle_and_replaces_retained_result_text():
    first = [message("user", "Original"), tool_call("old"), tool_result("old"),
        message("assistant", "Earlier"), message("user", "Next"), tool_call("c1"), tool_result("c1")]
    compressed = [message("user", "Original"), message("user", "Summary of old steps"),
        message("user", "Next"), tool_call("c1"),
        message("tool", "[compressed]", tool_call_id="c1", name="update"),
        message("assistant", "Final")]
    case = normalize_trajectory({"call_list": [{"messages": first},
        {"messages": compressed, "compressed": True}]}, context())
    assert len([e for e in case.events if e.kind == "tool_call"]) == 2
    assert len([e for e in case.events if e.kind == "tool_result"]) == 2
    assert any(e.content == "Summary of old steps" for e in case.events)
    assert case.response == "Final"
    assert not case.evidence_complete


def test_state_credential_fields_rejected_not_made_absent():
    with pytest.raises(ValueError, match="State snapshot contains a credential field"):
        normalize_trajectory({"messages": [message("assistant", "Done")]}, context(),
                             final_state={"password": "present", "count": 2})
    with pytest.raises(ValueError, match="State snapshot contains a credential field"):
        normalize_trajectory({"messages": [message("assistant", "Done")]}, context(),
                             initial_state={"account": {"api_key": "present"}})


def test_actual_raw_system_is_applied_before_call_harness_snapshot():
    declared = TaskContext(task_description="Update", harness={"system_prompt": "declared policy"})
    raw = {"system": "actual runtime policy", "messages": [message("user", "Update"),
        tool_call(), tool_result(), message("assistant", "Done")]}
    case = normalize_trajectory(raw, declared)
    call = next(e for e in case.events if e.kind == "tool_call")
    assert case.harness.system_prompt == "actual runtime policy"
    assert call.harness.system_prompt == "actual runtime policy"


def test_compression_does_not_swallow_structured_error_status_change():
    first = [message("user", "Update"), tool_call(), tool_result()]
    changed = [message("user", "Update"), tool_call(),
        message("tool", "compressed error", tool_call_id="c1", name="update", is_error=True),
        message("assistant", "Done")]
    # Contradictory repeated observations cannot be repaired as text-only compression.
    with pytest.raises(ValueError, match="Duplicate observed tool call"):
        normalize_trajectory({"call_list": [{"messages": first},
            {"messages": changed, "compressed": True}]}, context())
