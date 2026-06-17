from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from rexecop.connectors import errors as connector_errors
from rexecop.connectors.base import ConnectorRequest, ConnectorResponse
from rexecop.connectors.capability import connector_action_allowed
from rexecop.connectors.errors import READ_ONLY_MODES
from rexecop.connectors.mutating import MUTATING_ACTIONS
from rexecop.errors import RExecOpValidationError
from rexecop.evidence.redaction import redact_payload
from rexecop.secrets.port import SecretResolver
from rexecop.secrets.resolver import default_secret_resolver


class HttpApiConnectorRuntime:
    """Config-driven JSON HTTP connector backend."""

    def __init__(
        self,
        *,
        connector_name: str,
        config: dict[str, Any],
        profile_root: str | None,
        mutating_allowed: bool,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.connector_name = connector_name
        self.config = config
        self.profile_root = profile_root
        self.mutating_allowed = mutating_allowed
        self.secret_resolver = secret_resolver or default_secret_resolver()

    def invoke(self, request: ConnectorRequest) -> ConnectorResponse:
        if request.connector != self.connector_name:
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error="connector mismatch",
                data={"error_class": connector_errors.UNSUPPORTED},
            )

        profile_root = self._profile_root_path()
        if profile_root is not None and not connector_action_allowed(
            profile_root=profile_root,
            connector_name=request.connector,
            action=request.action,
        ):
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error="capability not declared in profile connector contract",
                data={"error_class": connector_errors.CAPABILITY_UNDECLARED},
            )

        action_spec = self._action_spec(request.action)
        if action_spec.get("mutating") or request.action in MUTATING_ACTIONS:
            if request.mode in READ_ONLY_MODES:
                return ConnectorResponse(
                    connector=request.connector,
                    action=request.action,
                    success=False,
                    error="mutating connector action refused in read-only mode",
                    data={"error_class": connector_errors.POLICY_DENIED},
                )
            if not self.mutating_allowed:
                return ConnectorResponse(
                    connector=request.connector,
                    action=request.action,
                    success=False,
                    error="mutating connector action blocked until GovEngine allows",
                    data={"error_class": connector_errors.POLICY_DENIED},
                )

        return self._invoke_with_retry(request, action_spec)

    def _invoke_with_retry(
        self,
        request: ConnectorRequest,
        action_spec: dict[str, Any],
    ) -> ConnectorResponse:
        retry_cfg = self.config.get("retry") or {}
        max_attempts = int(retry_cfg.get("max_attempts") or 1)
        allowed_on = {
            str(item) for item in (retry_cfg.get("on") or connector_errors.TRANSIENT_CLASSES)
        }
        last_response: ConnectorResponse | None = None
        for attempt in range(max_attempts):
            response = self._invoke_once(request, action_spec)
            if response.success:
                return response
            error_class = str(response.data.get("error_class") or "")
            last_response = response
            if error_class not in allowed_on or attempt + 1 >= max_attempts:
                return response
            time.sleep(min(0.05 * (attempt + 1), 0.2))
        assert last_response is not None
        return last_response

    def _invoke_once(
        self,
        request: ConnectorRequest,
        action_spec: dict[str, Any],
    ) -> ConnectorResponse:
        try:
            base_url = self._resolve_base_url()
            method = str(action_spec.get("method") or "GET").upper()
            path = self._render_template(str(action_spec.get("path") or "/"), request)
            url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
            query = action_spec.get("query")
            if isinstance(query, dict) and query:
                url = f"{url}?{urlencode({str(k): str(v) for k, v in query.items()})}"

            headers = {"Accept": "application/json"}
            headers.update(self._auth_headers())
            body = action_spec.get("body")
            data = None
            if body is not None:
                headers["Content-Type"] = "application/json"
                data = json.dumps(body).encode("utf-8")

            timeout = float(
                action_spec.get("timeout_seconds")
                or self.config.get("timeout_seconds")
                or 10
            )
            req = urllib.request.Request(url=url, method=method, headers=headers, data=data)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            if not isinstance(parsed, dict):
                parsed = {"value": parsed}
            unwrap = action_spec.get("unwrap")
            if isinstance(unwrap, str) and unwrap:
                extracted = parsed.get(unwrap)
                if isinstance(extracted, dict):
                    payload = dict(extracted)
                elif isinstance(extracted, list):
                    payload = {unwrap: extracted}
                else:
                    payload = {unwrap: extracted}
            else:
                payload = dict(parsed)
            payload = redact_payload(payload)
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=True,
                data=payload,
            )
        except urllib.error.HTTPError as exc:
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error=f"http error {exc.code}",
                data={
                    "error_class": connector_errors.AUTH_FAILED
                    if exc.code in {401, 403}
                    else connector_errors.TRANSIENT
                    if exc.code >= 500
                    else connector_errors.VALIDATION_FAILED,
                    "status_code": exc.code,
                },
            )
        except TimeoutError:
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error="connector timeout",
                data={"error_class": connector_errors.TIMEOUT},
            )
        except urllib.error.URLError as exc:
            reason = str(exc.reason)
            error_class = (
                connector_errors.TIMEOUT
                if "timed out" in reason.lower()
                else connector_errors.TRANSIENT
            )
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error=reason,
                data={"error_class": error_class},
            )
        except json.JSONDecodeError as exc:
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error=f"invalid json response: {exc}",
                data={"error_class": connector_errors.VALIDATION_FAILED},
            )
        except RExecOpValidationError as exc:
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error=str(exc),
                data={"error_class": connector_errors.VALIDATION_FAILED},
            )

    def _action_spec(self, action: str) -> dict[str, Any]:
        actions = self.config.get("actions")
        if not isinstance(actions, dict):
            raise RExecOpValidationError(
                f"http_api connector {self.connector_name} missing actions mapping"
            )
        spec = actions.get(action)
        if not isinstance(spec, dict):
            raise RExecOpValidationError(
                f"http_api action not configured: {self.connector_name}.{action}"
            )
        return spec

    def _resolve_base_url(self) -> str:
        if "base_url" in self.config:
            return str(self.config["base_url"])
        secret_ref = str(self.config.get("base_url_secret_ref") or "").strip()
        if secret_ref:
            return self.secret_resolver.resolve(secret_ref)
        raise RExecOpValidationError(
            f"http_api connector {self.connector_name} requires base_url or base_url_secret_ref"
        )

    def _auth_headers(self) -> dict[str, str]:
        auth = self.config.get("auth")
        if not isinstance(auth, dict):
            return {}
        secret_ref = str(auth.get("secret_ref") or "").strip()
        if not secret_ref:
            return {}
        value = self.secret_resolver.resolve(secret_ref)
        header = str(auth.get("header") or "Authorization")
        prefix = str(auth.get("prefix") or "")
        return {header: f"{prefix}{value}" if prefix else value}

    def _render_template(self, path: str, request: ConnectorRequest) -> str:
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        values = {
            "target": request.target,
            **{str(k): str(v) for k, v in metadata.items()},
        }
        return re.sub(
            r"\{([a-zA-Z0-9_]+)\}",
            lambda match: values.get(match.group(1), match.group(0)),
            path,
        )

    def _profile_root_path(self) -> Path | None:
        if not self.profile_root:
            return None
        return Path(self.profile_root)
