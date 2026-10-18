"""Training and deployment backends (SPEC 9.8). The loop runner only talks to these protocols.

Policy (SPEC 9.8): every generation trains a fresh LoRA adapter from the base model on the cumulative lineage
dataset, then makes it callable at an OpenAI-compatible endpoint. The Veri backend (preferred) and the vLLM fallback
implement these protocols; FakeTrainer/FakeDeployer exist only for tests and tiny-mode smoke runs without GPUs, and
their candidates are the base model itself (never report them).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from scar_schemas.models import ModelVersion


@dataclass(frozen=True)
class TrainedAdapter:
    adapter_uri: str  # where the LoRA weights live (Veri output, local path, ...)
    training_job_id: str


@dataclass(frozen=True)
class Deployment:
    endpoint_base_url: str
    endpoint_model_id: str  # value the agent sends as `model`


class Trainer(Protocol):
    def train(
        self, *, version: str, dataset_path: Path, n_examples: int, base_model_hf_id: str, seed: int
    ) -> TrainedAdapter:
        """Train a fresh LoRA from `base_model_hf_id` on the JSONL at dataset_path (SPEC 9.8 hyperparameters,
        training seed = lineage seed); block until done; raise on failure."""
        ...


class Deployer(Protocol):
    def deploy(self, *, version: str, adapter: TrainedAdapter, base: ModelVersion) -> Deployment:
        """Make the adapter callable at an OpenAI-compatible endpoint; raise on failure."""
        ...


class FakeTrainer:
    """No training: for tests and tiny-mode smoke runs only."""

    def train(
        self, *, version: str, dataset_path: Path, n_examples: int, base_model_hf_id: str, seed: int
    ) -> TrainedAdapter:
        if not dataset_path.exists():
            raise FileNotFoundError(dataset_path)
        return TrainedAdapter(adapter_uri=f"fake://{version}", training_job_id=f"fake-{version}")


class FakeDeployer:
    """Serves the base model's endpoint as the 'candidate': for tests and tiny-mode smoke runs only."""

    def deploy(self, *, version: str, adapter: TrainedAdapter, base: ModelVersion) -> Deployment:
        return Deployment(endpoint_base_url=base.endpoint_base_url, endpoint_model_id=base.endpoint_model_id)
