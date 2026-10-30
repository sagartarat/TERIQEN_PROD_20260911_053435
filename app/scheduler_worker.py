import os
import signal
import time

os.environ["ENABLE_BACKGROUND_SCHEDULER"] = "true"

from app import create_app  # noqa: E402

app = create_app()
_running = True


def _stop(*_args):
    global _running
    _running = False


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)

with app.app_context():
    app.logger.info("Dedicated TERIQEN scheduler worker started")

while _running:
    time.sleep(5)
