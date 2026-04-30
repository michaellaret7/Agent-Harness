"""
Download a model snapshot from the Hugging Face Hub into the local HF cache.

Once downloaded, vLLM (and transformers) will find the weights automatically
when you reference the same repo ID — no explicit path needed.

Usage examples:
    python scripts/download_model.py
    python scripts/download_model.py --model nvidia/NVIDIA-Nemotron-3-Nano-4B
    python scripts/download_model.py --model nvidia/Llama-3.1-Nemotron-Nano-8B-v1 --revision main
    python scripts/download_model.py --list-presets
    HF_TOKEN=hf_xxx python scripts/download_model.py --model some/gated-repo

Requires:
    pip install "huggingface_hub[hf_transfer]"
    # hf_transfer enables a faster Rust-based downloader; the script enables it
    # automatically if installed.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root so HF_TOKEN (and any other env vars) get
# picked up automatically when running `python scripts/download_model.py`.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# A few convenience presets. The --model flag accepts ANY HF repo ID; this is
# just a shortcut list so you don't have to memorize the exact strings.
PRESETS: dict[str, str] = {
    "nemotron-3-nano-4b":     "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16",
    "nemotron-3-nano-4b-fp8": "nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8",
    "nemotron-nano-8b":       "nvidia/Llama-3.1-Nemotron-Nano-8B-v1",
}

DEFAULT_MODEL = PRESETS["nemotron-3-nano-4b-fp8"]

# Project-local models directory. vLLM will read from here when we set
# HF_HOME=models in .env (or pass --download-dir to vllm serve).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = PROJECT_ROOT / "models"

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download a HuggingFace model snapshot into the local cache.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--model", "-m",
        default=DEFAULT_MODEL,
        help="HF repo ID (e.g. 'nvidia/NVIDIA-Nemotron-3-Nano-4B') OR a preset key.",
    )
    p.add_argument(
        "--revision", "-r",
        default=None,
        help="Git revision: branch, tag, or commit SHA.",
    )
    p.add_argument(
        "--cache-dir",
        default=str(DEFAULT_CACHE_DIR),
        help="HF cache directory. Default puts weights in ./models so they "
             "stay inside the project.",
    )
    p.add_argument(
        "--token",
        default=os.environ.get("HF_TOKEN"),
        help="HF access token for gated/private repos. Falls back to $HF_TOKEN.",
    )
    p.add_argument(
        "--allow-patterns",
        nargs="*",
        default=None,
        help="Only download files matching these glob patterns "
             "(e.g. '*.safetensors' '*.json' 'tokenizer*').",
    )
    p.add_argument(
        "--ignore-patterns",
        nargs="*",
        default=["*.bin", "*.pt", "*.pth", "*.gguf", "original/*"],
        help="Skip files matching these glob patterns. "
             "Default skips legacy .bin/.pt weights and GGUFs.",
    )
    p.add_argument(
        "--list-presets",
        action="store_true",
        help="Print the preset list and exit.",
    )
    p.add_argument(
        "--no-hf-transfer",
        action="store_true",
        help="Disable the hf_transfer fast downloader even if installed.",
    )
    return p.parse_args()


def resolve_model(name: str) -> str:
    if name in PRESETS:
        return PRESETS[name]
    if "/" not in name:
        sys.exit(
            f"error: '{name}' is neither a preset nor a 'org/repo' HF ID.\n"
            f"       Run with --list-presets to see shortcut keys."
        )
    return name


def maybe_enable_hf_transfer(disabled: bool) -> None:
    if disabled:
        os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
        return
    try:
        import hf_transfer  # noqa: F401
        os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")
    except ImportError:
        pass


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


def directory_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def main() -> int:
    args = parse_args()

    if args.list_presets:
        width = max(len(k) for k in PRESETS)
        print("Available preset keys (pass to --model):\n")
        for key, repo in PRESETS.items():
            print(f"  {key:<{width}}  ->  {repo}")
        return 0

    maybe_enable_hf_transfer(args.no_hf_transfer)

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        sys.exit(
            "error: huggingface_hub not installed.\n"
            "       Run: uv pip install 'huggingface_hub[hf_transfer]'"
        )

    repo_id = resolve_model(args.model)
    print(f"Downloading {repo_id}" + (f" @ {args.revision}" if args.revision else ""))
    if args.allow_patterns:
        print(f"  allow:  {args.allow_patterns}")
    if args.ignore_patterns:
        print(f"  ignore: {args.ignore_patterns}")

    local_path = snapshot_download(
        repo_id=repo_id,
        revision=args.revision,
        cache_dir=args.cache_dir,
        token=args.token,
        allow_patterns=args.allow_patterns,
        ignore_patterns=args.ignore_patterns,
    )

    path = Path(local_path)
    size = directory_size(path)
    print(f"\nDone. {human_size(size)} at:\n  {path}")
    print("\nvLLM will find this automatically when you launch with:")
    print(f"  vllm serve {repo_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
