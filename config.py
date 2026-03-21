"""
Configuration loader.

Reads all tuneable parameters from environment variables with sensible
defaults.  Import `load_config()` wherever a `Config` object is needed;
do not hard-code these values elsewhere.
"""

import os
from dataclasses import dataclass


@dataclass
class Config:
    knowledge_base_path: str  # directory that contains .md / .txt runbooks
    llm_provider: str         # "claude" | "local"
    model_name: str           # passed to the active provider
    top_k: int                # number of KB chunks to retrieve per query
    chunk_size: int           # max characters per chunk


def load_config() -> Config:
    return Config(
        knowledge_base_path=os.getenv("KNOWLEDGE_BASE_PATH", "knowledge_base"),
        llm_provider=os.getenv("LLM_PROVIDER", "claude"),
        model_name=os.getenv("MODEL_NAME", "claude-sonnet-4-6"),
        top_k=int(os.getenv("TOP_K", "3")),
        chunk_size=int(os.getenv("CHUNK_SIZE", "500")),
    )
