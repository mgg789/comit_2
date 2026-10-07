"""Run the verification-engine development server."""

import os

import uvicorn

from verification_engine.main import create_app

uvicorn.run(create_app(), host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8105")))
