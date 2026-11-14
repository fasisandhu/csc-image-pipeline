"""Who is asking. The one replaceable identity source: today a localhost request is the owner.

Teammate access (Tailscale or Cloudflare Access) replaces this module at the team-access step.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

LOCAL_HOSTS = {"127.0.0.1", "::1", "testclient"}  # "testclient": FastAPI's TestClient


def current_user(request: Request) -> str:
    host = request.client.host if request.client else None
    if host in LOCAL_HOSTS:
        return "owner"
    raise HTTPException(403, "only local requests are allowed")
