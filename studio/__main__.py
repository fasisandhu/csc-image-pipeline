"""python -m studio: the dashboard server on http://127.0.0.1:8765 plus its job worker."""
from __future__ import annotations

from contextlib import closing

import uvicorn
from dotenv import load_dotenv

from pipeline.compile import ROOT

from . import db
from .app import app
from .worker import Worker

HOST, PORT = "127.0.0.1", 8765


def main() -> None:
    load_dotenv(ROOT / ".env")
    with closing(db.connect()) as conn:
        n = db.mark_interrupted(conn)
    if n:
        print(f"{n} job(s) were running when the server stopped; marked interrupted, not re-run")
    Worker(db.connect).start()
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
