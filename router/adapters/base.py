from __future__ import annotations

from abc import ABC, abstractmethod

from router.core.models import GenerationRequest, GenerationResult, ProviderCapability


class BaseAdapter(ABC):
    provider_name: str
    model_name: str

    @abstractmethod
    def capability(self) -> ProviderCapability:
        raise NotImplementedError

    @abstractmethod
    def build_request(self, request: GenerationRequest) -> dict:
        raise NotImplementedError

    @abstractmethod
    def generate(self, request: GenerationRequest) -> GenerationResult:
        raise NotImplementedError
