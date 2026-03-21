import json

from triage.loader import load_documents
from triage.chunker import chunk_documents
from triage.retriever import TFIDFRetriever

_PROMPT_TEMPLATE = """\
You are a Kubernetes incident triage assistant.
Using ONLY the context below, analyze the incident and respond with a JSON object.

=== RETRIEVED CONTEXT ===
{context}
=== END CONTEXT ===

=== INCIDENT ===
{error_log}
=== END INCIDENT ===

Respond with ONLY a valid JSON object — no markdown, no explanation — with these keys:
- "summary": string, a concise description of the likely root cause
- "confidence": number between 0.0 and 1.0
- "action_items": list of strings, concrete remediation steps
- "sources": list of source file paths referenced from the context

Do not invent information not present in the context.\
"""

_LOW_SCORE_NOTE = (
    " Note: retrieved context has low relevance scores — the knowledge base "
    "may not cover this incident type."
)


_NORMALIZE_PROMPT = (
    "Extract the core error from this input and return a clean 1-2 sentence description "
    "of what went wrong, suitable for searching a technical knowledge base. "
    "Return only the description, nothing else."
)


_NO_KB_PROMPT = (
    "No knowledge base context available. Use your general knowledge to analyze this incident "
    "but be clear that no internal documentation was found.\n\n"
    "=== INCIDENT ===\n{error_log}\n=== END INCIDENT ===\n\n"
    "Respond with ONLY a valid JSON object with these keys:\n"
    '- "summary": concise description of the likely root cause\n'
    '- "confidence": set this to 0.1\n'
    '- "action_items": list of concrete remediation steps\n'
    '- "sources": set this to []\n'
    "Do not use markdown or prose outside the JSON."
)


def _parse_llm_response(raw: str) -> dict:
    import re
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {"summary": raw, "confidence": 0.0, "action_items": [], "sources": []}


def _run_without_kb(error_log: str, config) -> dict:
    prompt = _NO_KB_PROMPT.format(error_log=error_log)
    raw = config.llm_provider.complete(prompt)
    response = _parse_llm_response(raw)
    return {
        "summary": response.get("summary", ""),
        "confidence": 0.1,
        "action_items": response.get("action_items", []),
        "sources": [],
    }


def normalize_incident(raw_input: str, provider) -> str:
    prompt = f"{_NORMALIZE_PROMPT}\n\n{raw_input}"
    return provider.complete(prompt).strip()


def run_triage(error_log: str, config) -> dict:
    documents = load_documents(config.knowledge_base_path)
    chunks = chunk_documents(documents)

    if not chunks:
        return _run_without_kb(error_log, config)

    retriever = TFIDFRetriever()
    retriever.build_index(chunks)

    normalized_query = normalize_incident(error_log, config.llm_provider)

    top_k = getattr(config, "top_k", 3)
    results = retriever.retrieve(normalized_query, top_k=top_k)

    weak_retrieval = all(r["score"] < 0.1 for r in results)

    context_parts = []
    for i, chunk in enumerate(results, start=1):
        context_parts.append(
            f"[{i}] (source: {chunk['source']}, score: {chunk['score']:.3f})\n{chunk['content']}"
        )
    context = "\n\n".join(context_parts) if context_parts else "(no relevant context found)"

    prompt = _PROMPT_TEMPLATE.format(
        context=context,
        error_log=error_log,
    )

    raw = config.llm_provider.complete(prompt)
    response = _parse_llm_response(raw)

    if weak_retrieval:
        response["confidence"] = min(response.get("confidence", 0.0), 0.3)
        response["summary"] = response.get("summary", "") + _LOW_SCORE_NOTE

    response.setdefault("sources", [r["source"] for r in results])

    return {
        "summary": response.get("summary", ""),
        "confidence": response.get("confidence", 0.0),
        "action_items": response.get("action_items", []),
        "sources": response.get("sources", []),
    }
