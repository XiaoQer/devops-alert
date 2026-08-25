from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        default_max_bytes: int,
        path_limits: dict[str, int] | None = None,
    ) -> None:
        self._app = app
        self._default_max_bytes = default_max_bytes
        self._path_limits = path_limits or {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        max_bytes = self._path_limits.get(scope["path"], self._default_max_bytes)
        declared_length = _content_length(scope)
        if declared_length is not None and declared_length > max_bytes:
            await _too_large(scope, receive, send)
            return

        messages: list[Message] = []
        received_bytes = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.disconnect":
                break
            received_bytes += len(message.get("body", b""))
            if received_bytes > max_bytes:
                await _too_large(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        async def replay_body() -> Message:
            if messages:
                return messages.pop(0)
            return {"type": "http.request", "body": b"", "more_body": False}

        await self._app(scope, replay_body, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _too_large(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse(
        status_code=413,
        content={"code": "request_too_large", "message": "请求体超过该接口允许的大小"},
    )
    await response(scope, receive, send)
