"""Shared slowapi Limiter instance.

Imported by server.py (registered on app.state) and any router that
needs rate-limit decorators, so all limits share the same counter backend.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
