import json

import anthropic

# The Anthropic client reads ANTHROPIC_API_KEY from the environment automatically.
# Keeping the client at module level avoids re-initialising it on every request.
client = anthropic.Anthropic()

# ---------------------------------------------------------------------------
# Mock tool: get_troubleshooting_docs
# ---------------------------------------------------------------------------
# Why a mock? We want the agent to demonstrate real tool-use round-trips without
# requiring a live docs API. Hardcoded data is enough for the ReAct loop to work
# end-to-end and for unit tests to stay deterministic.

_DOCS: dict[str, list[str]] = {
    "CrashLoopBackOff": [
        "Check previous container logs: kubectl logs <pod> --previous",
        "Inspect exit code in kubectl describe pod <pod>",
        "Verify the container entrypoint/command is correct in the manifest",
        "Check resource limits — OOM kills surface as CrashLoopBackOff",
        "Review recent changes: kubectl rollout history deployment/<name>",
    ],
    "OOMKilled": [
        "Increase memory limits in the pod spec (resources.limits.memory)",
        "Profile in-container memory with heap dumps or pprof",
        "Check for memory leaks in application code",
        "Tune JVM heap (-Xmx) if running a JVM-based workload",
        "Monitor live usage: kubectl top pod <pod>",
    ],
    "ImagePullBackoff": [
        "Verify the image name and tag are correct in the pod spec",
        "Confirm the image registry is reachable from cluster nodes",
        "Check imagePullSecrets are configured for private registries",
        "Test manually: docker pull <image>",
        "Read the exact error: kubectl describe pod <pod> → Events section",
    ],
}


def get_troubleshooting_docs(error_type: str) -> str:
    """Return hardcoded troubleshooting steps for a Kubernetes error type as JSON."""
    steps = _DOCS.get(error_type, [
        "No specific docs found. Use kubectl describe and kubectl logs for details.",
    ])
    return json.dumps({"error_type": error_type, "steps": steps})


# ---------------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------------
# We pass this to the Anthropic API so Claude knows the tool exists, what it
# does, and what parameters to supply.  The description is intentionally
# detailed — Claude uses it to decide *when* and *how* to call the tool.

_TOOLS: list[dict] = [
    {
        "name": "get_troubleshooting_docs",
        "description": (
            "Retrieve recommended troubleshooting steps for a known Kubernetes error type. "
            "Always call this before forming your final answer so your action items are "
            "grounded in documented remediation guidance."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "error_type": {
                    "type": "string",
                    "description": (
                        "The Kubernetes error type to look up. "
                        "Examples: CrashLoopBackOff, OOMKilled, ImagePullBackoff."
                    ),
                }
            },
            "required": ["error_type"],
        },
    }
]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
# We instruct Claude to:
#   1. Always call the tool before answering (ReAct: reason → act → observe → respond).
#   2. Return ONLY a raw JSON object on its final turn — no markdown, no prose —
#      so we can parse it with json.loads() without any stripping.

_SYSTEM = """\
You are a Kubernetes SRE assistant. Your job is to diagnose a pod error from its log \
and produce a structured remediation report.

Workflow (follow this exactly):
1. Read the error log and identify the most likely Kubernetes error type.
2. Call get_troubleshooting_docs with that error type.
3. Use the returned steps to inform your answer.
4. Reply with ONLY a JSON object — no markdown fences, no extra text — with these keys:
   - "summary": one sentence describing the root cause in plain English
   - "confidence_score": integer 0–100 reflecting your diagnostic confidence
   - "action_items": list of concrete remediation strings

Example final response:
{"summary": "Pod is OOMKilled because it exceeds its memory limit.", \
"confidence_score": 92, \
"action_items": ["Increase resources.limits.memory.", "Profile heap usage."]}
"""

# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

def _dispatch(name: str, inputs: dict) -> str:
    if name == "get_troubleshooting_docs":
        return get_troubleshooting_docs(**inputs)
    return json.dumps({"error": f"Unknown tool: {name}"})


# ---------------------------------------------------------------------------
# ReAct loop
# ---------------------------------------------------------------------------

def run_triage_agent(error_log: str) -> dict:
    """
    Run a ReAct-style triage loop using the Anthropic Messages API.

    The loop:
      send(error_log) → Claude may call get_troubleshooting_docs
      → we execute the tool and send the result back
      → Claude returns a JSON diagnosis

    Returns a dict with keys:
      summary        (str)   — one-sentence root-cause description
      confidence_score (int) — 0–100
      action_items   (list[str]) — concrete remediation steps
    """
    messages: list[dict] = [
        {"role": "user", "content": f"Error log:\n\n{error_log}"}
    ]

    while True:
        # Why streaming? kubectl logs can be large; streaming prevents HTTP
        # timeouts on long inputs without requiring us to handle every event.
        # get_final_message() blocks until the stream is complete and returns
        # the assembled Message object — best of both worlds.
        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=4096,
            system=_SYSTEM,
            tools=_TOOLS,
            thinking={"type": "adaptive"},
            messages=messages,
        ) as stream:
            response = stream.get_final_message()

        # Always append the full content list — not just the text block.
        # Tool-use blocks must be preserved so the API can match them to
        # the tool_result we send in the next turn.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # Claude is done.  Extract the JSON text block and parse it.
            text = next(
                (block.text for block in response.content if block.type == "text"),
                "",
            )
            result = json.loads(text)
            return {
                "summary": str(result["summary"]),
                "confidence_score": int(result["confidence_score"]),
                "action_items": list(result["action_items"]),
            }

        if response.stop_reason == "tool_use":
            # Execute every tool Claude requested, then send all results back
            # in a single user turn (required by the API — one tool_result
            # message can contain multiple results).
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    output = _dispatch(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,  # must match the tool_use block id
                        "content": output,
                    })

            messages.append({"role": "user", "content": tool_results})
            # Continue the loop — Claude will read the tool result and
            # produce its final JSON answer on the next turn.
            continue

        raise RuntimeError(f"Unexpected stop_reason: {response.stop_reason!r}")
