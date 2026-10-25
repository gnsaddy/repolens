"""Pick the embedding provider for a run: the local hash embedder, or any OpenAI-compatible /embeddings endpoint
(OpenAI, Gemini's compatible endpoint, Ollama, vLLM, an embedding server...)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EmbedderSpec:
    name: str = "hash"
    provider: str = "hash"  # hash | openai
    base_url: str = ""
    model: str = ""
    api_key_env: str = ""  # name of the environment variable that holds the key (the key itself is never an argument)
    dim: int = 256
    send_dimensions: bool = True

    def apply(self) -> None:
        """Point the application settings at this embedder for the current process."""
        from ...config import get_settings
        from ...services import answer_memory

        os.environ["REPOLENS_EMBEDDING_PROVIDER"] = self.provider
        os.environ["REPOLENS_EMBEDDING_DIM"] = str(self.dim)
        os.environ["REPOLENS_EMBEDDING_SEND_DIMENSIONS"] = "true" if self.send_dimensions else "false"
        if self.provider != "hash":
            os.environ["REPOLENS_EMBEDDING_BASE_URL"] = self.base_url
            os.environ["REPOLENS_EMBEDDING_MODEL"] = self.model
            os.environ["REPOLENS_EMBEDDING_API_KEY"] = os.environ.get(self.api_key_env, "") if self.api_key_env else ""
        get_settings.cache_clear()
        answer_memory._embed_cached.cache_clear()


HASH = EmbedderSpec()
