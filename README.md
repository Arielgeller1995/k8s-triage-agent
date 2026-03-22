# incident-triage-agent

A retrieval-augmented incident triage service. Paste any error log or incident description, get back a structured diagnosis grounded in your own runbooks — no hallucination, full source attribution.

---

## Project Overview

incident-triage-agent accepts any error log or incident description and returns a JSON response with a root-cause summary, confidence score, concrete action items, and the runbook files used. It retrieves the most relevant sections from a local knowledge base before calling Claude, so answers are grounded in your own documentation. The service is stateless and domain-agnostic — the knowledge base ships with Kubernetes runbooks as a demo, but works for any system you have runbooks for.

---

## How It Works

1. **Load** — scans the `knowledge_base/` folder for `.md` and `.txt` runbooks
2. **Chunk** — splits documents into overlapping windows for fine-grained matching
3. **Retrieve** — scores your error log against every chunk and picks the top N matches (N is configurable, equals 3 by default)
4. **Prompt** — builds a grounded prompt from the retrieved context and the error log
5. **Respond** — Claude returns structured JSON (includes incident summary, confidence level, action items and relevant sources); if retrieval is weak, confidence is capped at 30% and a warning is appended. If the knowledge base is empty, the service skips retrieval entirely and falls back to Claude's general knowledge with confidence set to 10% 

---

## Prerequisites

**Required:**
- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/) (or set `LLM_PROVIDER=local` to skip)

**Required for Kubernetes deployment:**
- Docker
- kind (or any similar tool) + kubectl

---

## Quick Start

### Option A — Run Locally (fastest)

```bash
git clone https://github.com/Arielgeller1995/incident-triage-agent
cd incident-triage-agent

python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

export ANTHROPIC_API_KEY=your-key-here
uvicorn main:app --reload --port 8000
```

Visit http://localhost:8000/docs for the interactive Swagger UI.

### Option B — Run on Kubernetes (production-like)

```bash
git clone https://github.com/Arielgeller1995/incident-triage-agent
cd incident-triage-agent

# Build and load image
docker build -t incident-triage-agent:latest .
kind load docker-image incident-triage-agent:latest

# Create secret and deploy
kubectl create secret generic anthropic-secret \
  --from-literal=api-key=your-key-here
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml

# Verify and forward
kubectl get pods -l app=incident-triage-agent
kubectl port-forward svc/incident-triage-agent 8000:8000
```

Visit http://localhost:8000/docs for the interactive Swagger UI.

---

## How to Build

```bash
# 1. Clone the repo
git clone https://github.com/Arielgeller1995/incident-triage-agent
cd incident-triage-agent

# 2. Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set the API key
export ANTHROPIC_API_KEY=your-key-here

# 5. Run locally
uvicorn main:app --reload --port 8000
```

The service is now running at http://localhost:8000.

---

## How to Deploy on Kubernetes

```bash
# 1. Build the Docker image
docker build -t incident-triage-agent:latest .

# 2. Load into kind (local dev cluster)
kind load docker-image incident-triage-agent:latest

# 3. Create the API key secret
kubectl create secret generic anthropic-secret \
  --from-literal=api-key=your-key-here

# 4. Apply manifests
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml

# 5. Verify the pod is running
kubectl get pods -l app=incident-triage-agent
kubectl logs -l app=incident-triage-agent --tail=50
```

---

## How to Run the App Once Deployed

```bash
# 1. Port-forward the service
kubectl port-forward svc/incident-triage-agent 8000:8000

# 2. Open Swagger UI
# Visit http://localhost:8000/docs in your browser

# 3. Send a test request via curl
curl -s -X POST http://localhost:8000/triage \
  -H "Content-Type: text/plain" \
  --data "Back-off restarting failed container payments-api in namespace production" | jq .

# 4. Check the health endpoint
curl http://localhost:8000/health
# {"status":"ok"}
```

---

## Knowledge Base

The `knowledge_base/` folder contains Markdown runbooks.

**To add your own runbooks:** drop any `.md` or `.txt` file into the folder and restart the server. No indexing step required — the service loads and indexes on startup.

**Demo vs Production:** For the demo, the `knowledge_base/` folder is baked into the Docker image. In production, mount it as an external volume so runbooks can be updated without rebuilding the image. Use `-v $(pwd)/knowledge_base:/app/knowledge_base` with Docker or a ConfigMap/PersistentVolume in Kubernetes.

**Empty knowledge base:** If the folder is empty, the service skips retrieval entirely and falls back to Claude's general knowledge with confidence capped at 10% and empty sources.

As an example, the service ships with seven `.md` files by default (editable at any time):
```
knowledge_base/
├── crashloopbackoff.md
├── database_connection.md
├── api_authentication.md
├── imagepullbackoff.md
├── oomkilled.md
├── probe_failures.md
└── pending_pods.md
```
---

## API Reference

### `POST /triage`

**Request** — send the raw error log as a plain text body:

```bash
curl -s -X POST http://localhost:8000/triage \
  -H "Content-Type: text/plain" \
  --data "Back-off restarting failed container payments-api ..." | jq .
```

**Response:**

```json
{
  "summary": "The payments-api container is crashing on startup because it cannot authenticate with PostgreSQL. The Secret containing the database password is likely missing or has an incorrect key.",
  "confidence": "91%",
  "action_items": [
    "kubectl get secret postgres-credentials -n production -o yaml",
    "Verify the DB_PASSWORD key exists and matches the PostgreSQL user's actual password",
    "kubectl rollout restart deployment/payments-api -n production"
  ],
  "sources": ["knowledge_base/crashloopbackoff.md"]
}
```

### `GET /health`

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

---

## Architecture

The service is built around a clean pipeline with separated concerns:

| Component | File | Responsibility |
|-----------|------|----------------|
| API layer | `main.py` | FastAPI routes, request validation, response formatting |
| Configuration | `config.py` | Loads all settings from environment variables |
| Pipeline | `triage/pipeline.py` | Orchestrates all steps end to end |
| Loader | `triage/loader.py` | Reads `.md` and `.txt` files from the knowledge base |
| Chunker | `triage/chunker.py` | Splits documents into overlapping windows |
| Retriever | `triage/retriever.py` | TF-IDF index + cosine similarity search |
| LLM Provider | `triage/providers/` | Abstraction layer — swap Claude for any model |

**Request flow:**
1. Raw input received → Claude normalizes it into a clean search query
2. Loader reads KB files → Chunker splits them → Retriever finds top matches
3. If no matches found → skip to Claude with general knowledge fallback
4. Matched chunks assembled into grounded prompt → sent to LLM provider
5. Structured JSON returned with summary, confidence, action items, sources
