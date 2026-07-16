import json
import socket
import urllib.error
import urllib.request

from main.llm.errors import (
    ApiAuthError,
    ApiConfigError,
    ApiConnectionError,
    ApiHTTPError,
    ApiTimeoutError,
)


def post_json(url: str, payload: dict, headers: dict, timeout: int = 120) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        message = f"API error {exc.code}: {body}"
        if exc.code in {401, 403}:
            raise ApiAuthError(message) from exc
        if exc.code == 404:
            raise ApiConfigError(message) from exc
        raise ApiHTTPError(message) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise ApiTimeoutError(f"API timeout: {exc}") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise ApiTimeoutError(f"API timeout: {reason}") from exc
        raise ApiConnectionError(f"Connection error: {reason}") from exc
