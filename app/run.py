import os
from dotenv import load_dotenv

from app import create_app
from app.common.logger import setup_logging

load_dotenv(override=False)
loggers = setup_logging()
app = create_app()

if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "False").lower() == "true"

    loggers["app"].info(
        "Starting TERIQEN on %s:%s | debug=%s", host, port, debug
    )
    app.run(host=host, port=port, debug=debug)
