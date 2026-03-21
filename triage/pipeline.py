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


def run_triage(incident: dict, config) -> dict:
    documents = load_documents(config.knowledge_base_path)
    chunks = chunk_documents(documents)

    retriever = TFIDFRetriever()
    retriever.build_index(chunks)

    top_k = getattr(config, "top_k", 3)
    results = retriever.retrieve(incident["error_log"], top_k=top_k)

    weak_retrieval = all(r["score"] < 0.1 for r in results)

    context_parts = []
    for i, chunk in enumerate(results, start=1):
        context_parts.append(
            f"[{i}] (source: {chunk['source']}, score: {chunk['score']:.3f})\n{chunk['content']}"
        )
    context = "\n\n".join(context_parts) if context_parts else "(no relevant context found)"

    prompt = _PROMPT_TEMPLATE.format(
        context=context,
        error_log=incident["error_log"],
    )

    raw = config.llm_provider.complete(prompt)

    try:
        response = json.loads(raw)
    except json.JSONDecodeError:
        # Attempt to extract a JSON object if the model wrapped it in prose
        import re
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            response = json.loads(match.group())
        else:
            response = {
                "summary": raw,
                "confidence": 0.0,
                "action_items": [],
                "sources": [],
            }

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
