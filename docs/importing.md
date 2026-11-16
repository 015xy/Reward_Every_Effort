# Importing recorded conversations

`case_from_messages` converts one recorded OpenAI-style user turn into a `Case`
that can be labeled, scored, and inspected by the general reward framework.

For a JSON object, JSON array, or JSONL dataset, select one zero-based record:

```bash
ree normalize conversations.json --index 0 --output outputs/case.json
ree score outputs/case.json --output outputs/score.json --html outputs/score.html
```

Normalization makes no model calls. Scoring runs the configured Labeler and judges;
open the resulting English or Chinese HTML, or load the result JSON in Inspector.
Large source datasets are read by the CLI rather than loaded into the report page.

```python
from ree.conversation import case_from_messages

record = {
    "messages": [
        {"role": "system", "content": "Report only observed facts."},
        {"role": "user", "content": "Read the configuration."},
        {
            "role": "assistant",
            "content": "",
            "tools": [{
                "type": "function",
                "function": {
                    "name": "read_config",
                    "description": "Read a configuration file.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            }],
            "tool_calls": [{
                "id": "call-a",
                "type": "function",
                "function": {
                    "name": "read_config",
                    "arguments": '{"path":"config.json"}',
                },
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "call-a",
            "content": '{  "enabled" : true }\n',
        },
        {"role": "assistant", "content": "The setting is enabled."},
    ]
}
case = case_from_messages(record)
```

The adapter imports only the `messages` field. Top-level annotations, prior
rubrics, labels, user metadata, and provider identifiers stay in the original
input and do not become task requirements or scoring facts. Requirements are
subsequently derived by Labeler from the actual user message.

## Content and tool visibility

The first assistant's visible system instructions and explicit `tools` list form
`Case.harness`. Multiple preceding system messages retain their full text in
order, separated by two newlines. Tools declared in later assistant messages
replace the currently visible tool set; they never become available earlier.
An absent `tools` field inherits the previous set, while `tools: []` explicitly
removes all tools.

Events whose visible harness differs from the initial harness carry an explicit
`Event.harness` snapshot. An absent event snapshot inherits `Case.harness`, not
the preceding event. Tool results carry the snapshot of their associated call,
including when results arrive after a later tool declaration. Later system
instructions are preserved in context events and subsequent harness snapshots.
The adapter does not infer skills, permissions, or compression rules from text.

Assistant content preceding a tool call remains a message event. The final
response is the terminal assistant's `content`, and `reasoning_content` is
excluded. A changed final harness is retained as a context event without copying
the final response into another message. Tool `content` stays an exact string:
JSON is not reserialized, and formatting, whitespace, and full results remain
available as evidence. Execution errors are not guessed from provider metadata,
keywords, or JSON fields; judges can read the original observed result.

## Association and incomplete evidence

Tool call IDs only associate recorded calls and results. They never become
subject IDs. Event references are deterministic one-based source positions:
`msg-3-call-1` identifies the first call in the third message, and `result-4`
identifies the fourth message's tool result. Parallel results can arrive in a
different order and still reference the correct call.

An unmatched call or a trajectory ending before an assistant response sets
`evidence_complete=False`. A terminal tool result is not promoted to an assistant
reply. Orphan results, forward references, duplicate call IDs or results, and
conflicting tool names fail explicitly. Missing tool names on results are filled
only from the matched observed call.

Function `arguments` may be an object or a JSON string containing an object.
Malformed JSON, arrays, null arguments, ambiguous duplicate keys, and non-finite
numbers fail with their message and call positions. Missing or invalid schemas
are not replaced with invented declarations.

The current importer supports exactly one user message and text content with
`system`, `user`, `assistant`, and `tool` roles. Multiple user turns, prior
assistant history, unsupported roles, and multimodal content fail explicitly;
they are never silently flattened or dropped. This normalization step performs
no tool execution, model calls, or reward computation.
