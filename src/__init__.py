"""Application package for the FastAPI service.

The FastAPI entry point lives in ``src.app`` (the ``app`` factory + the
lifespan that wires the DB engine, the resilience layer, and the request
audit log). ``src.common`` holds settings/enums; ``src.core`` holds the
reusable infrastructure; the ``model``/``repository``/``schema``/``service``
packages hold your domain code.
"""
