import os

import uvicorn

from testing_engine.main import create_app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8102"))
    uvicorn.run(create_app(), host="0.0.0.0", port=port)
