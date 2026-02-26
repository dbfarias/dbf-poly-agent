"""Application-layer rate limiting (defense-in-depth alongside nginx)."""

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

# Disable rate limiting during tests to avoid spurious 429s.
_enabled = os.environ.get("TESTING", "").lower() not in ("1", "true")

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"], enabled=_enabled)
