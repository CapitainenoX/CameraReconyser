"""Point d'entree : demarre FastAPI (uvicorn) puis la fenetre native pywebview."""
from __future__ import annotations

import asyncio
import socket
import threading
import time

import uvicorn
import webview

from app import models
from app.config import SOUNDS_DIR
from app.server import app, state


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = _free_port()
HOST = "127.0.0.1"


def _run_server() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    state.loop = loop
    cfg = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", loop="asyncio")
    server = uvicorn.Server(cfg)
    loop.run_until_complete(server.serve())


def _wait_ready(timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def main() -> None:
    models.ensure_default_alarm(SOUNDS_DIR)
    threading.Thread(target=_run_server, daemon=True).start()
    _wait_ready()
    # declencheurs au demarrage
    threading.Timer(1.0, state.router.fire_startup).start()
    window = webview.create_window(
        "Camera Recognizer",
        f"http://{HOST}:{PORT}/",
        width=1200, height=820, min_size=(960, 640),
    )

    def on_closed() -> None:
        state.camera.stop()
        state.voice.stop()
        state.actions.alarm.stop()

    window.events.closed += on_closed
    webview.start()


if __name__ == "__main__":
    main()
