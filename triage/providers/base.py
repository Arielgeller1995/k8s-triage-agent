from abc import ABC, abstractmethod


class BaseLLMProvider(ABC):
    @abstractmethod
    def complete(self, prompt: str) -> str:
        ...
