"""Entry point for `python main.py` and uvicorn."""

from __future__ import annotations

import os

import uvicorn

from aiforen.app import app  # noqa: F401  (re-exported for "main:app" target)

if __name__ == "__main__":
    uvicorn.run(
        "aiforen.app:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("DEBUG", "false").lower() == "true",
        log_level="info",
        access_log=True,
    )
