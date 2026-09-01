from __future__ import annotations

import json as json_module
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field


class FeishuConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    app_id: str = Field(min_length=1, max_length=128)
    app_secret: str = Field(min_length=1, max_length=256)
    base_url: str = "https://open.feishu.cn"
    timeout_seconds: float = Field(default=5.0, gt=0, le=30)


class FeishuHttpResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status_code: int
    body: dict[str, object]


class FeishuMessageResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: str = Field(min_length=1, max_length=128)


class FeishuTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout_seconds: float,
    ) -> FeishuHttpResponse: ...


@dataclass(frozen=True, slots=True)
class FeishuRetryableError(Exception):
    error_code: str


@dataclass(frozen=True, slots=True)
class FeishuPermanentError(Exception):
    error_code: str


class UrllibFeishuTransport:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout_seconds: float,
    ) -> FeishuHttpResponse:
        body = json_module.dumps(json, ensure_ascii=False).encode()
        request = Request(
            url,
            data=body,
            method=method,
            headers={"Content-Type": "application/json", **headers},
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                response_body = response.read(1_048_577)
                if len(response_body) > 1_048_576:
                    raise FeishuPermanentError("response_too_large")
                parsed = json_module.loads(response_body or b"{}")
                return FeishuHttpResponse(
                    status_code=response.status,
                    body=parsed if isinstance(parsed, dict) else {},
                )
        except HTTPError as error:
            return FeishuHttpResponse(status_code=error.code, body={})
        except (URLError, TimeoutError) as error:
            raise FeishuRetryableError("transport_error") from error


class FeishuClient:
    def __init__(
        self,
        config: FeishuConfig,
        *,
        transport: FeishuTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibFeishuTransport()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._tenant_token: str | None = None
        self._tenant_token_expires_at: datetime | None = None

    def get_tenant_token(self) -> str:
        now = self._clock().astimezone(UTC)
        if (
            self._tenant_token is not None
            and self._tenant_token_expires_at is not None
            and now < self._tenant_token_expires_at - timedelta(seconds=60)
        ):
            return self._tenant_token
        response = self._transport.request(
            "POST",
            f"{self._config.base_url}/open-apis/auth/v3/tenant_access_token/internal",
            headers={},
            json={"app_id": self._config.app_id, "app_secret": self._config.app_secret},
            timeout_seconds=self._config.timeout_seconds,
        )
        self._raise_for_error(response)
        token = response.body.get("tenant_access_token")
        expire = response.body.get("expire")
        if not isinstance(token, str) or not token or not isinstance(expire, int):
            raise FeishuPermanentError("invalid_token_response")
        self._tenant_token = token
        self._tenant_token_expires_at = now + timedelta(seconds=max(expire, 60))
        return token

    def send_incident_card(
        self,
        *,
        chat_id: str,
        card: dict[str, object],
    ) -> FeishuMessageResult:
        return self._send_message(
            "/open-apis/im/v1/messages?receive_id_type=chat_id",
            {
                "receive_id": chat_id,
                "msg_type": "interactive",
                "content": _compact_json(card),
            },
        )

    def update_incident_card(
        self,
        *,
        message_id: str,
        card: dict[str, object],
    ) -> None:
        response = self._authorized_request(
            "PATCH",
            f"/open-apis/im/v1/messages/{message_id}",
            {"content": _compact_json(card)},
        )
        self._raise_for_error(response)

    def reply_to_thread(
        self,
        *,
        root_message_id: str,
        text: str,
    ) -> FeishuMessageResult:
        return self._send_message(
            f"/open-apis/im/v1/messages/{root_message_id}/reply",
            {"msg_type": "text", "content": _compact_json({"text": text})},
        )

    def _send_message(
        self,
        path: str,
        payload: dict[str, object],
    ) -> FeishuMessageResult:
        response = self._authorized_request("POST", path, payload)
        self._raise_for_error(response)
        data = response.body.get("data")
        message_id = data.get("message_id") if isinstance(data, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise FeishuPermanentError("invalid_message_response")
        return FeishuMessageResult(message_id=message_id)

    def _authorized_request(
        self,
        method: str,
        path: str,
        payload: dict[str, object],
    ) -> FeishuHttpResponse:
        return self._transport.request(
            method,
            f"{self._config.base_url}{path}",
            headers={"Authorization": f"Bearer {self.get_tenant_token()}"},
            json=payload,
            timeout_seconds=self._config.timeout_seconds,
        )

    @staticmethod
    def _raise_for_error(response: FeishuHttpResponse) -> None:
        code = response.body.get("code")
        if 200 <= response.status_code < 300 and code in (None, 0):
            return
        error_code = str(code)[:64] if code is not None else f"http_{response.status_code}"
        if response.status_code == 429 or response.status_code >= 500:
            raise FeishuRetryableError(error_code)
        raise FeishuPermanentError(error_code)


def _compact_json(value: dict[str, object]) -> str:
    return json_module.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "FeishuClient",
    "FeishuConfig",
    "FeishuHttpResponse",
    "FeishuMessageResult",
    "FeishuPermanentError",
    "FeishuRetryableError",
    "FeishuTransport",
    "UrllibFeishuTransport",
]
