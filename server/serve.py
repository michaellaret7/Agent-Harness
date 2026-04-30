"""
Launch the local vLLM OpenAI-compatible server.

Usage:
    uv run python -m server.serve

Once running, the API is available at:
    http://localhost:8000/v1
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from server.config import MODELS_DIR, PROJECT_ROOT, ServerConfig

load_dotenv(PROJECT_ROOT / ".env")


def find_reasoning_parser_plugin(model_repo: str) -> Path | None:
    """Locate the `nano_v3_reasoning_parser.py` plugin in the model snapshot."""
    repo_dir = MODELS_DIR / f"models--{model_repo.replace('/', '--')}"
    return next(repo_dir.glob("snapshots/*/nano_v3_reasoning_parser.py"), None)


def main() -> None:
    config = ServerConfig()
    config.reasoning_parser_plugin = find_reasoning_parser_plugin(config.model)
    
    if config.reasoning_parser_plugin is None:
        sys.exit(f"Run scripts/download_model.py first — no snapshot under {MODELS_DIR}")

    args = ["vllm", *config.to_cli_args()]
    print("$ " + " ".join(args), flush=True)
    os.execvp(args[0], args)


if __name__ == "__main__":
    main()
