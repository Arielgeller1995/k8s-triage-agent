from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import load_config
from triage.pipeline import run_triage
from triage.providers.claude import ClaudeProvider
from triage.providers.local import LocalProvider

# --- startup -----------------------------------------------------------

_raw_config = load_config()

if _raw_config.llm_provider == "claude":
    _provider = ClaudeProvider(model_name=_raw_config.model_name)
else:
    _provider = LocalProvider()

# Attach the instantiated provider so pipeline.py can call .complete()
_raw_config.llm_provider = _provider  # type: ignore[assignment]
config = _raw_config

# --- app ---------------------------------------------------------------

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- schemas -----------------------------------------------------------

class TriageRequest(BaseModel):
    error_log: str
    component: str | None = None
    severity: str | None = None


class TriageResponse(BaseModel):
    summary: str
    confidence: float
    action_items: list[str]
    sources: list[str]


# --- endpoints ---------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/triage", response_model=TriageResponse)
def triage(request: TriageRequest):
    try:
        result = run_triage(request.model_dump(), config)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return TriageResponse(**result)
