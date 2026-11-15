"""python -m studio: the dashboard server on http://127.0.0.1:8765 plus its job worker.

The port is bound before anything else, so a second copy exits without touching the job store.
"""
from __future__ import annotations

import socket
import sys
from contextlib import closing

import uvicorn
from dotenv import load_dotenv

from pipeline.compile import ROOT

from . import db
from .app import app
from .brain import warm
from .worker import Worker

HOST, PORT = "127.0.0.1", 8765


def bind(port: int) -> socket.socket:
    """A listening socket on HOST:port that no other process can share. Raises OSError if taken."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:  # POSIX: quick restart after TIME_WAIT; a live listener still blocks the bind
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, port))
        sock.listen()
    except OSError:
        sock.close()
        raise
    return sock


def serve(sock: socket.socket) -> None:
    uvicorn.Server(uvicorn.Config(app, host=HOST, port=sock.getsockname()[1])).run(sockets=[sock])


def main(port: int = PORT) -> int:
    try:
        sock = bind(port)
    except OSError:
        print(f"CSC Studio is already running on http://{HOST}:{port} (or the port is in use); "
              "not starting a second copy.", file=sys.stderr)
        return 1
    with sock:
        load_dotenv(ROOT / ".env")
        with closing(db.connect()) as conn:
            n = db.mark_interrupted(conn)
        if n:
            print(f"{n} job(s) or review step(s) were running when the server stopped; marked, not re-run")
        Worker(db.connect).start()
        print(f"CSC Studio on http://{HOST}:{sock.getsockname()[1]} (Ctrl+C to stop)")  # uvicorn skips its own line for passed sockets
        try:
            serve(sock)
        finally:
            warm.shutdown()  # close the warm Claude sessions and their Claude processes
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
