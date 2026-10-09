"""Entrypoint: python -m code_judge (serves on CODE_JUDGE_PORT, default 8105)."""

import os

import uvicorn

from code_judge.main import create_app

if __name__ == "__main__":
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("CODE_JUDGE_PORT", "8105")))
