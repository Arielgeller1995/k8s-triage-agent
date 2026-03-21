from triage.providers.base import BaseLLMProvider


class LocalProvider(BaseLLMProvider):
    def complete(self, prompt: str) -> str:
        return "Local models are not yet implemented."
