import os

import anthropic

from triage.providers.base import BaseLLMProvider


class ClaudeProvider(BaseLLMProvider):
    def __init__(self, model_name: str = "claude-opus-4-6"):
        self._model_name = model_name
        self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def complete(self, prompt: str) -> str:
        response = self._client.messages.create(
            model=self._model_name,
            max_tokens=16000,
            messages=[{"role": "user", "content": prompt}],
        )
        return next(block.text for block in response.content if block.type == "text")
