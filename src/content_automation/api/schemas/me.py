"""Response schema for GET /api/me. Never includes anything credential-
shaped — a UserRecord has no such field, but this is still an explicit,
narrow response model (not `return user.__dict__`) so a future column
added to `users` doesn't silently start being returned to the client."""

from pydantic import BaseModel


class MeResponse(BaseModel):
    id: int
    email: str
    display_name: str | None
