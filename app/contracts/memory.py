"""Memory records that may be recalled into Planner context."""

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.types import NonEmptyStr


class MemoryItem(BaseModel):
    """A bounded memory record supplied as context, never as an instruction."""

    model_config = ConfigDict(extra="forbid")

    memory_id: NonEmptyStr
    content: NonEmptyStr
    scope: list[NonEmptyStr] = Field(default_factory=list)
