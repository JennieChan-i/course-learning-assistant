import os
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("HF_HOME", str(ROOT / ".cache" / "huggingface"))
os.environ.setdefault("TORCH_HOME", str(ROOT / ".cache" / "torch"))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", env_ignore_empty=True)
    app_mode: str = "demo"
    app_port: int = 8765
    dataset: str = "sample"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_context_tokens: int = 16384
    llm_max_output_tokens: int = 1800
    llm_temperature: float | None = None
    llm_reasoning_effort: Literal["low", "medium", "high", "max"] | None = None
    llm_coach_reasoning_effort: Literal["low", "medium", "high", "max"] | None = None
    llm_timeout_seconds: float = 120
    llm_structured_output_tokens: int = 3500
    llm_input_price_per_million: float | None = None
    llm_output_price_per_million: float | None = None
    tavily_api_key: str = ""
    tavily_allow_keyless: bool = False
    search_monthly_limit: int = 300
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    rerank_model: str = "BAAI/bge-reranker-base"
    device: str = "auto"
    local_inference_batch_size: int = 8

    @property
    def runtime(self) -> Path:
        mode = "live" if self.app_mode == "live" else "demo"
        data = "private" if self.dataset == "private" else "sample"
        p = ROOT / "runtime" / f"{mode}-{data}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def course_path(self) -> Path:
        if self.dataset == "private":
            return ROOT / "chunk" / "all_course_chunks_clean_v1.csv"
        return ROOT / "data" / "sample_courses.csv"

    @property
    def llm_ready(self) -> bool:
        return bool(self.llm_api_key and self.llm_model and self.llm_base_url)


settings = Settings()
