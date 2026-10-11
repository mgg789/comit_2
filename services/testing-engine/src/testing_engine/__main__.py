import os

import uvicorn

from testing_engine.main import create_app, create_testing_app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8102"))
    app = create_testing_app()
    # Keep session authentication middleware and retention on the root app.
    app.include_router(create_app().router)
    uvicorn.run(app, host="0.0.0.0", port=port)
