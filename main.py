from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class TriageRequest(BaseModel):
    error_log: str


class TriageResponse(BaseModel):
    summary: str
    confidence_score: float
    action_items: list[str]


@app.post("/triage", response_model=TriageResponse)
def triage(request: TriageRequest):
    return TriageResponse(
        summary="Pod is crash-looping due to an unhandled exception in the application.",
        confidence_score=0.85,
        action_items=[
            "Check recent deployments for breaking changes.",
            "Review pod logs for the full stack trace.",
            "Verify environment variables and secrets are correctly mounted.",
        ],
    )
