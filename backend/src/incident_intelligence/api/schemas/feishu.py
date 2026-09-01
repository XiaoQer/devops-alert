from pydantic import BaseModel, ConfigDict


class FeishuCallbackResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: str
    challenge: str | None = None


__all__ = ["FeishuCallbackResponse"]
