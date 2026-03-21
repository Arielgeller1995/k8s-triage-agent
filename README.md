# k8s-triage-agent

A retrieval-augmented Kubernetes incident triage service. Given an error log, it retrieves the most relevant runbook sections from a local knowledge base, grounds a structured LLM prompt in that context, and returns a JSON response with a root-cause summary, confidence score, concrete action items, and source attribution.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Runtime Flow](#3-runtime-flow)
4. [How TF-IDF Retrieval Works](#4-how-tf-idf-retrieval-works)
5. [Grounding and Fallback](#5-grounding-and-fallback)
6. [Confidence Estimation](#6-confidence-estimation)
7. [Configuration](#7-configuration)
8. [Swapping LLM Providers](#8-swapping-llm-providers)
9. [Adding a UI](#9-adding-a-ui)
10. [Prerequisites and Quick Start](#10-prerequisites-and-quick-start)
11. [Docker](#11-docker)
12. [Kubernetes](#12-kubernetes)
13. [Limitations and Future Work](#13-limitations-and-future-work)

---

## 1. Project Overview

On-call engineers spend significant time diagnosing failures that match well-known patterns. This service automates the first step: given a raw error log, it finds the matching runbook content and asks Claude to produce a structured diagnosis — without hallucinating information that isn't in the runbook.

**Key properties:**

- **Grounded** — the LLM is instructed to prioritize retrieved context. When relevant docs are found, it answers only from them. When retrieval is weak, it falls back to general knowledge but signals this with a lower confidence score.
- **Auditable** — every response includes the source files used
- **Provider-agnostic** — any backend that implements a one-method interface can replace Claude
- **Stateless** — no database required; the knowledge base is plain Markdown files on disk

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                        FastAPI                          │
│  POST /triage          GET /health                      │
└──────────────────────────┬──────────────────────────────┘
                           │ incident dict
                           ▼
┌─────────────────────────────────────────────────────────┐
│                   triage/pipeline.py                    │
│                                                         │
│  loader  ──►  chunker  ──►  retriever  ──►  prompt      │
│                                              │          │
│                                              ▼          │
│                                       LLM provider      │
│                                              │          │
│                                              ▼          │
│                                       JSON response     │
└─────────────────────────────────────────────────────────┘

k8s-triage-agent/
├── main.py
├── config.py
├── triage/
│   ├── pipeline.py
│   ├── loader.py
│   ├── chunker.py
│   ├── retriever.py
│   └── providers/
│       ├── base.py
│       ├── claude.py
│       └── local.py
├── knowledge_base/
│   ├── crashloopbackoff.md
│   ├── imagepullbackoff.md
│   ├── oomkilled.md
│   ├── probe_failures.md
│   └── pending_pods.md
├── incidents/
│   └── sample.json
├── k8s/
│   ├── deployment.yaml
│   ├── service.yaml
│   └── secret.yaml
├── Dockerfile
└── requirements.txt
```

---

## 3. Runtime Flow

```
Request body
  { "error_log": "...", "component": "payments-api", "severity": "critical" }
         │
         ▼
① loader.load_documents(config.knowledge_base_path)
     Scans knowledge_base/ recursively for .md and .txt files.
     Returns [{ "content": "...", "source": "path/to/file.md" }, ...]

         │
         ▼
② chunker.chunk_documents(documents, chunk_size=500, overlap=50)
     Splits each document into overlapping character windows.
     Returns [{ "content": "...", "source": "...", "chunk_index": 0 }, ...]

         │
         ▼
③ retriever.build_index(chunks)  →  retriever.retrieve(error_log, top_k=3)
     Builds a TF-IDF matrix over all chunks.
     Scores the error_log query against every chunk via cosine similarity.
     Returns the top-k chunks with their similarity scores.

         │
         ▼
④ Prompt construction
     Assembled from:
       • Retrieved chunks (numbered, with source path and score)
       • The original error_log
       • Instruction to answer ONLY from the provided context

         │
         ▼
⑤ provider.complete(prompt)
     Calls the configured LLM (default: Claude via Anthropic API).
     Expects a raw JSON string back.

         │
         ▼
⑥ JSON parsing + confidence adjustment
     Parses the response as JSON.
     If all retrieval scores < 0.1 → caps confidence at 0.3 and appends a warning.
     Normalises keys to: summary, confidence, action_items, sources.

         │
         ▼
Response body
  {
    "summary": "Root cause: ...",
    "confidence": 0.87,
    "action_items": ["kubectl logs ...", "Check secret ..."],
    "sources": ["knowledge_base/crashloopbackoff.md"]
  }
```

---

## 4. How TF-IDF Retrieval Works

TF-IDF (Term Frequency–Inverse Document Frequency) scores how relevant a term is to a document relative to a corpus:

```
TF(t, d)  = (occurrences of term t in chunk d) / (total terms in d)
IDF(t)    = log( N / df(t) )   where N = total chunks, df(t) = chunks containing t

TF-IDF(t, d) = TF(t, d) × IDF(t)
```

Each chunk becomes a sparse vector of TF-IDF weights. The query (error log) is transformed into the same vector space, and **cosine similarity** ranks chunks by the angle between their vectors:

```
similarity(query, chunk) = (query · chunk) / (‖query‖ × ‖chunk‖)
```

Cosine similarity ranges from 0 (no shared terms) to 1 (identical term distributions). The `top_k` highest-scoring chunks are passed to the LLM as context.

**Why TF-IDF instead of embeddings?**

| Property | TF-IDF | Embeddings |
|---|---|---|
| Setup | No model download, no GPU | Requires encoder model |
| Speed | Microseconds | Tens of milliseconds |
| Semantic matching | Keyword-level only | Handles synonyms / paraphrasing |
| Explainability | Exact term overlap | Opaque latent space |

For a small, keyword-rich runbook corpus TF-IDF is fast, lightweight, and easy to debug. Swapping in a semantic retriever is a one-class change (see [§8](#8-swapping-llm-providers)).

---

## 5. Grounding and Fallback

The prompt instructs the model to prioritize retrieved context over its general knowledge:
```
Using the context below as your primary source, analyze the incident.
Prefer the retrieved context over your general knowledge.
If the context is insufficient, you may use general knowledge but 
reflect this with a lower confidence score.
```

The system behaves differently based on retrieval quality:

**Strong retrieval (score > 0.1):**
Claude answers primarily from your runbooks. The response is grounded, traceable, and sources are cited.

**Weak retrieval (all scores < 0.1):**
Claude supplements with its own training knowledge as fallback. The pipeline applies a hard constraint:

1. **Caps confidence at `0.3`** — signals low trustworthiness to the caller
2. **Appends a note to `summary`** — `"...Note: retrieved context has low relevance scores — the knowledge base may not cover this incident type."`
3. **Returns empty sources** — `[]` signals the answer did not come from your runbooks

This makes the degraded state explicit. A high confidence score means the answer is traceable to a specific runbook. A low confidence score means Claude is supplementing from general knowledge — the engineer should verify before acting.

In a production ops tool, false confidence is more dangerous than admitting uncertainty.
---

## 6. Confidence Estimation

Confidence is produced by the LLM in its JSON response, but the pipeline applies a hard constraint based on retrieval quality:

```python
# pipeline.py
weak_retrieval = all(r["score"] < 0.1 for r in results)

if weak_retrieval:
    response["confidence"] = min(response.get("confidence", 0.0), 0.3)
```

The LLM is instructed to set confidence based on how completely the retrieved context explains the incident. In practice:

| Scenario | Expected confidence |
|---|---|
| Exact match (e.g. "CrashLoopBackOff" → `crashloopbackoff.md`) | 0.7 – 0.95 |
| Partial match (related content, not exact) | 0.4 – 0.7 |
| No relevant content (all scores < 0.1) | ≤ 0.3 (hard cap) |

---

## 7. Configuration

All runtime parameters are read from environment variables. No config file needs to be edited.

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required when `LLM_PROVIDER=claude`)* | Anthropic API key |
| `LLM_PROVIDER` | `claude` | `claude` or `local` |
| `MODEL_NAME` | `claude-sonnet-4-6` | Model passed to the active provider |
| `KNOWLEDGE_BASE_PATH` | `knowledge_base` | Directory containing `.md` / `.txt` runbooks |
| `TOP_K` | `3` | Number of chunks to retrieve per query |
| `CHUNK_SIZE` | `500` | Max characters per chunk |

### Setting the API key

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Or create a `.env` file and load it:

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
LLM_PROVIDER=claude
MODEL_NAME=claude-sonnet-4-6
TOP_K=5
```

```bash
set -a && source .env && set +a
```

---

## 8. Swapping LLM Providers

All LLM interaction goes through the `BaseLLMProvider` interface:

```python
# triage/providers/base.py
from abc import ABC, abstractmethod

class BaseLLMProvider(ABC):
    @abstractmethod
    def complete(self, prompt: str) -> str:
        ...
```

To add a new provider — for example, a local Ollama server — create `triage/providers/ollama.py`:

```python
import requests
from triage.providers.base import BaseLLMProvider

class OllamaProvider(BaseLLMProvider):
    def __init__(self, model_name: str = "llama3", base_url: str = "http://localhost:11434"):
        self._model = model_name
        self._url = f"{base_url}/api/generate"

    def complete(self, prompt: str) -> str:
        resp = requests.post(self._url, json={"model": self._model, "prompt": prompt, "stream": False})
        resp.raise_for_status()
        return resp.json()["response"]
```

Then register it in `main.py`:

```python
from triage.providers.ollama import OllamaProvider

if _raw_config.llm_provider == "ollama":
    _provider = OllamaProvider(model_name=_raw_config.model_name)
```

Set `LLM_PROVIDER=ollama` at runtime — no other changes required.

To swap the retriever (e.g. to sentence-transformers embeddings), implement a class with `build_index(chunks)` and `retrieve(query, top_k)` and substitute it in `pipeline.py`.

---

## 9. Adding a UI

The API is CORS-enabled for all origins, so any frontend can talk to it directly.

**Minimal React example:**

```jsx
async function triage(errorLog) {
  const res = await fetch("http://localhost:8000/triage", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ error_log: errorLog, severity: "critical" }),
  });
  return res.json();
  // { summary, confidence, action_items, sources }
}
```

**Suggested UI components:**

- A multi-line textarea for pasting error logs
- A confidence badge (colour-coded: green ≥ 0.7, amber 0.3–0.7, red < 0.3)
- An ordered checklist rendered from `action_items`
- Collapsible source attribution panel showing which runbook files were used

The service is stateless — the UI does not need a session or auth layer to get started.

---

## 10. Prerequisites and Quick Start

**Requirements:**

- Python 3.11+
- An Anthropic API key (or set `LLM_PROVIDER=local` to use the placeholder)

```bash
# 1. Clone and enter the project
git clone <repo-url>
cd k8s-triage-agent

# 2. Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Export your API key
export ANTHROPIC_API_KEY="sk-ant-..."

# 5. Start the server
uvicorn main:app --reload --port 8000
```

**Send a triage request:**

```bash
curl -s -X POST http://localhost:8000/triage \
  -H "Content-Type: application/json" \
  -d @incidents/sample.json | jq .
```

Expected response shape:

```json
{
  "summary": "The payments-api container is crashing on startup because it cannot authenticate with PostgreSQL. The Secret containing the database password is likely missing, empty, or has an incorrect key.",
  "confidence": 0.91,
  "action_items": [
    "kubectl get secret postgres-credentials -n production -o yaml",
    "Verify the DB_PASSWORD key exists and matches the PostgreSQL user's actual password",
    "kubectl rollout restart deployment/payments-api -n production"
  ],
  "sources": ["knowledge_base/crashloopbackoff.md"]
}
```

**Health check:**

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

---

## 11. Docker

```bash
# Build
docker build -t k8s-triage-agent:latest .

# Run
docker run -p 8000:8000 \
  -e ANTHROPIC_API_KEY="sk-ant-..." \
  -v $(pwd)/knowledge_base:/app/knowledge_base \
  k8s-triage-agent:latest
```

The knowledge base is mounted as a volume so you can update runbooks without rebuilding the image.

---

## 12. Kubernetes

### Create the API key secret

```bash
kubectl create secret generic anthropic-secret \
  --from-literal=api-key="sk-ant-..."
```

### Load the image into kind (local development)

```bash
kind load docker-image k8s-triage-agent:latest
```

### Apply manifests

```bash
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
```

### Verify

```bash
kubectl get pods -l app=k8s-triage-agent
kubectl logs -l app=k8s-triage-agent --tail=50
```

### Forward and test

```bash
kubectl port-forward svc/k8s-triage-agent 8000:8000
curl -s http://localhost:8000/health
```

### Providing the knowledge base in-cluster

For production, bake the `knowledge_base/` directory into the image or mount a ConfigMap / PersistentVolume. The default deployment uses the image-bundled copy. To mount runbooks from a ConfigMap:

```yaml
volumes:
  - name: kb
    configMap:
      name: triage-runbooks
containers:
  - name: k8s-triage-agent
    volumeMounts:
      - name: kb
        mountPath: /app/knowledge_base
env:
  - name: KNOWLEDGE_BASE_PATH
    value: /app/knowledge_base
```

---

## 13. Limitations and Future Work

### Current Limitations

| Area | Limitation |
|---|---|
| **Retrieval** | TF-IDF is keyword-based; semantic paraphrasing ("pod won't start" vs "container fails to initialize") may not match well |
| **Knowledge base** | Loaded and indexed on every request — no caching; slow on large corpora |
| **Confidence** | Score is LLM-generated and weakly calibrated; it should not be treated as a probability |
| **Context window** | All top-k chunks are concatenated; very large chunks may crowd out useful content |
| **No feedback loop** | There is no mechanism to log whether the triage was correct and improve retrieval over time |
| **Single-turn** | The pipeline does not support follow-up questions or multi-turn diagnosis |

### Future Work

- **Semantic retrieval** — replace TF-IDF with a sentence-transformer encoder (e.g. `all-MiniLM-L6-v2`) for paraphrase-aware matching
- **Persistent index** — build the TF-IDF / embedding index once at startup and refresh on file changes with a watchdog
- **Feedback logging** — store requests and engineer ratings in a SQLite or Postgres database; use ratings to re-rank retrieved chunks
- **Streaming responses** — switch to server-sent events so the UI can display the summary as it is generated
- **Multi-turn chat** — maintain conversation history so engineers can ask follow-up questions about the same incident
- **Automatic KB updates** — watch a Git repository or S3 bucket for runbook changes and hot-reload the index
- **Prometheus metrics** — expose request latency, retrieval score distribution, and confidence histogram at `/metrics`
- **Auth** — add an API key header or JWT middleware for production deployments
