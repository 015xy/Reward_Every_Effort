"""Normalize one recorded OpenAI-style user turn into observable scoring evidence.

Source annotations and transport identifiers are not task requirements. This
adapter copies only conversation content and explicitly supplied tool schemas.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from typing import Any

from .models import Case, Event, Harness, Tool


def _json_value(value: Any) -> None:
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _json_value(item)
        return
    raise ValueError("Tool arguments and schemas must contain finite JSON values")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate tool argument keys are ambiguous")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON constant {value} is not supported")


def _arguments(value: Any, message: int, call: int) -> dict[str, Any]:
    try:
        parsed = (
            json.loads(value, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            if isinstance(value, str)
            else deepcopy(value)
        )
        if not isinstance(parsed, dict):
            raise ValueError("Function arguments must be a JSON object")
        _json_value(parsed)
        return parsed
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError(
            f"Message {message}, call {call}: invalid function arguments: {exc}"
        ) from exc


def _tools(value: Any, message: int) -> list[Tool]:
    if not isinstance(value, list):
        raise ValueError(f"Message {message}: tools must be a list of function declarations")
    tools = []
    for index, declaration in enumerate(value, 1):
        if not isinstance(declaration, dict) or declaration.get("type") != "function":
            raise ValueError(f"Message {message}, tool {index}: only function tools are supported")
        function = declaration.get("function")
        if not isinstance(function, dict):
            raise ValueError(
                f"Message {message}, tool {index}: function declaration must be an object"
            )
        parameters = deepcopy(function.get("parameters"))
        _json_value(parameters)
        tools.append(
            Tool(
                name=function.get("name"),
                description=function.get("description", ""),
                parameters=parameters,
            )
        )
    # Also checks duplicate tool names, including an explicitly empty snapshot.
    return Harness(tools=tools).tools


def _content(message: dict[str, Any], index: int, *, nullable: bool = False) -> str:
    value = message.get("content")
    if value is None and nullable:
        return ""
    if not isinstance(value, str):
        raise ValueError(
            f"Message {index}: content must be text; multimodal content is unsupported"
        )
    return value


def case_from_messages(record: dict) -> Case:
    """Adapt one user turn without importing external labels or guessing evidence.

    Message positions and call positions are one-based. Tool call IDs are used
    only for observed call/result association, never for requirement subjects.
    Every event inherits ``Case.harness`` unless it carries a different explicit
    snapshot. Tool result content stays an exact string, including JSON spacing.
    """
    if not isinstance(record, dict) or not isinstance(record.get("messages"), list):
        raise ValueError("Expected an object containing a messages list")
    messages = record["messages"]
    if not messages or any(not isinstance(message, dict) for message in messages):
        raise ValueError("messages must be a nonempty list of message objects")
    supported = {"system", "user", "assistant", "tool"}
    if any(message.get("role") not in supported for message in messages):
        raise ValueError("Only system, user, assistant and tool message roles are supported")
    users = [index for index, message in enumerate(messages) if message["role"] == "user"]
    if len(users) != 1:
        raise ValueError(f"Exactly one user turn is supported; found {len(users)} user messages")
    user_index = users[0]
    if any(message["role"] != "system" for message in messages[:user_index]):
        raise ValueError(
            "Messages before the user turn must be system messages; history is unsupported"
        )
    request = _content(messages[user_index], user_index + 1)
    if not request:
        raise ValueError("The user request must be nonempty")
    assistant_indices = [
        index for index, message in enumerate(messages) if message["role"] == "assistant"
    ]
    if not assistant_indices:
        raise ValueError("The recorded turn must contain an assistant message")
    first_assistant = assistant_indices[0]
    if any(message["role"] == "tool" for message in messages[:first_assistant]):
        raise ValueError("A tool result has no preceding assistant call")
    system_parts = [
        _content(message, index + 1)
        for index, message in enumerate(messages[:first_assistant])
        if message["role"] == "system"
    ]
    initial_tools = (
        _tools(messages[first_assistant]["tools"], first_assistant + 1)
        if "tools" in messages[first_assistant]
        else []
    )
    baseline = Harness(system_prompt="\n\n".join(system_parts), tools=initial_tools)
    current = baseline.model_copy(deep=True)
    events: list[Event] = []
    calls: dict[str, tuple[str, str, Harness]] = {}
    resolved: set[str] = set()
    final_index = (
        len(messages) - 1
        if messages[-1]["role"] == "assistant" and not messages[-1].get("tool_calls")
        else None
    )
    response = ""

    def snapshot(harness: Harness) -> Harness | None:
        return None if harness == baseline else harness.model_copy(deep=True)

    for index, message in enumerate(messages):
        number = index + 1
        role = message["role"]
        if index < first_assistant:
            # Already preserved verbatim in request and the initial system prompt.
            continue
        if role == "system":
            text = _content(message, number)
            system_parts.append(text)
            current = current.model_copy(
                update={"system_prompt": "\n\n".join(system_parts)}, deep=True
            )
            events.append(
                Event(
                    ref=f"msg-{number}-context",
                    kind="context",
                    content=text,
                    harness=snapshot(current),
                )
            )
            continue
        if role == "assistant":
            if "tools" in message:
                current = current.model_copy(
                    update={"tools": _tools(message["tools"], number)}, deep=True
                )
            text = _content(message, number, nullable=True)
            tool_calls = message.get("tool_calls")
            if tool_calls is None:
                tool_calls = []
            if not isinstance(tool_calls, list):
                raise ValueError(f"Message {number}: tool_calls must be a list")
            if index == final_index:
                response = text
                if current != baseline:
                    events.append(
                        Event(
                            ref=f"msg-{number}-context", kind="context", harness=snapshot(current)
                        )
                    )
            elif text or not tool_calls:
                events.append(
                    Event(
                        ref=f"msg-{number}", kind="message", content=text, harness=snapshot(current)
                    )
                )
            for call_index, call in enumerate(tool_calls, 1):
                if not isinstance(call, dict) or call.get("type") != "function":
                    raise ValueError(
                        f"Message {number}, call {call_index}: only function calls are supported"
                    )
                call_id, function = call.get("id"), call.get("function")
                if not isinstance(call_id, str) or not call_id or call_id in calls:
                    raise ValueError(
                        f"Message {number}, call {call_index}: call IDs must be nonempty and unique"
                    )
                if (
                    not isinstance(function, dict)
                    or not isinstance(function.get("name"), str)
                    or not function["name"]
                ):
                    raise ValueError(
                        f"Message {number}, call {call_index}: function name must be nonempty"
                    )
                name, ref = function["name"], f"msg-{number}-call-{call_index}"
                arguments = _arguments(function.get("arguments"), number, call_index)
                events.append(
                    Event(
                        ref=ref,
                        kind="tool_call",
                        tool=name,
                        arguments=arguments,
                        harness=snapshot(current),
                    )
                )
                calls[call_id] = (ref, name, current.model_copy(deep=True))
        elif role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in calls:
                raise ValueError(f"Message {number}: tool result has no matching preceding call")
            if call_id in resolved:
                raise ValueError(f"Message {number}: duplicate tool result for one call")
            ref, name, call_harness = calls[call_id]
            if "name" in message and message["name"] != name:
                raise ValueError(
                    f"Message {number}: tool result name conflicts with its associated call"
                )
            events.append(
                Event(
                    ref=f"result-{number}",
                    kind="tool_result",
                    tool=name,
                    call_ref=ref,
                    output=_content(message, number),
                    harness=snapshot(call_harness),
                )
            )
            resolved.add(call_id)

    return Case(
        request=request,
        response=response,
        events=events,
        harness=baseline,
        evidence_complete=final_index is not None and len(resolved) == len(calls),
    )
