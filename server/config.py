"""
Configuration for the local vLLM server.

Defaults follow NVIDIA's recommended launch command for
NVIDIA-Nemotron-3-Nano-4B-FP8 (see model README), scaled for a 16 GB consumer
GPU. Override any field by editing this file or by setting the corresponding
environment variable in .env (handled in serve.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "models"


@dataclass
class ServerConfig:
    # --- Model ---
    model: str = "nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8"
    served_model_name: str = "nemotron3-nano-4b-fp8"

    # --- Network ---
    host: str = "0.0.0.0"
    port: int = 8000

    # --- Capacity / memory ---
    # NVIDIA recommends max_model_len=262144, but that needs >24 GB. 16384 is
    # a safe starting point on a 16 GB card; raise it as VRAM allows.
    max_model_len: int = 16384
    max_num_seqs: int = 8
    gpu_memory_utilization: float = 0.90
    tensor_parallel_size: int = 1

    # --- Cache dtypes (memory savings) ---
    kv_cache_dtype: str = "fp8"          # quantize KV cache too
    mamba_ssm_cache_dtype: str = "float32"  # SSM state stays in fp32 for accuracy

    # --- Reasoning + tool calling ---
    # Nemotron 3 emits <think>...</think> reasoning blocks (DeepSeek-R1 style)
    # parsed by a custom plugin shipped with the model snapshot.
    reasoning_parser: str = "nano_v3"
    # Set at runtime by serve.py once it locates the snapshot.
    reasoning_parser_plugin: Path | None = None

    # The model was post-trained to emit tool calls in Qwen3-Coder's format.
    tool_call_parser: str = "qwen3_coder"
    enable_auto_tool_choice: bool = True

    # --- Misc ---
    trust_remote_code: bool = True   # required: model ships custom modeling code
    enforce_eager: bool = False      # CUDA graphs on; flip True if hybrid Mamba+Attn breaks
    download_dir: Path = field(default_factory=lambda: MODELS_DIR)

    # FlashInfer (vLLM's default pick on Ada) JIT-compiles kernels and needs
    # nvcc; this box has the driver but no CUDA toolkit. TRITON_ATTN compiles
    # via the driver and works without nvcc.
    attention_backend: str = "TRITON_ATTN"

    def to_cli_args(self) -> list[str]:
        """Render this config as a list of `vllm serve ...` CLI arguments."""
        args: list[str] = [
            "serve", self.model,
            "--served-model-name", self.served_model_name,
            "--host", self.host,
            "--port", str(self.port),
            "--max-model-len", str(self.max_model_len),
            "--max-num-seqs", str(self.max_num_seqs),
            "--gpu-memory-utilization", str(self.gpu_memory_utilization),
            "--tensor-parallel-size", str(self.tensor_parallel_size),
            "--kv-cache-dtype", self.kv_cache_dtype,
            "--mamba_ssm_cache_dtype", self.mamba_ssm_cache_dtype,
            "--reasoning-parser", self.reasoning_parser,
            "--tool-call-parser", self.tool_call_parser,
            "--download-dir", str(self.download_dir),
            "--attention-backend", self.attention_backend,
        ]
        if self.trust_remote_code:
            args.append("--trust-remote-code")
        if self.enforce_eager:
            args.append("--enforce-eager")
        if self.enable_auto_tool_choice:
            args.append("--enable-auto-tool-choice")
        if self.reasoning_parser_plugin is not None:
            args += ["--reasoning-parser-plugin", str(self.reasoning_parser_plugin)]
        return args
