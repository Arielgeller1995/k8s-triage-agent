from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agent import run_triage_agent

app = FastAPI()


class TriageRequest(BaseModel):
    error_log: str


class TriageResponse(BaseModel):
    summary: str
    confidence_score: int  # 0–100 integer; changed from float to match agent output
    action_items: list[str]


@app.post("/triage", response_model=TriageResponse)
def triage(request: TriageRequest):
    # Delegate entirely to the LLM agent.  Any exception surfaces as a 500.
    try:
        result = run_triage_agent(request.error_log)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return TriageResponse(**result)
