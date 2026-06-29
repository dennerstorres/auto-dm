"""Web backend package — Phase 26.

Exposes a FastAPI app that wraps the existing CLI/narrative stack.
Auth uses JWT (Authorization: Bearer <token>); saves are persisted in
Postgres; active game sessions are kept in Redis.
"""
