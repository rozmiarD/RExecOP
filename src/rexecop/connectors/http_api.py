from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from rexecop.connectors import errors as connector_errors
from rexecop.connectors.action_shape import validate_http_action_shape
from rexecop.connectors.base import (
    ConnectorRequest,
    ConnectorResponse,
    effective_output_bytes,
    effective_timeout_seconds,
)
from rexecop.connectors.capability import connector_action_allowed
from rexecop.connectors.errors import READ_ONLY_MODES
from rexecop.connectors.http_support import (
    get_json_path,
    http_error_class,
    merge_paginated_items,
    read_http_error_body,
    resolve_next_url,
    resolve_retry_config,
    retry_delay_seconds,
)
from rexecop.connectors.mutating import MUTATING_ACTIONS
from rexecop.errors import RExecOpValidationError
from rexecop.evidence.redaction import redact_payload, redact_text, register_secret_value
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

        try:
            action_spec = self._action_spec(request.action)
            action_contract_digest = self._validate_action_shape(request.action)
        except RExecOpValidationError as exc:
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error=redact_text(str(exc)),
                data={"error_class": connector_errors.VALIDATION_FAILED},
            )
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

        response = self._invoke_with_retry(request, action_spec)
        if action_contract_digest:
            response.data["action_contract_digest"] = action_contract_digest
        return response

    def _validate_action_shape(self, action: str) -> str | None:
        profile_root = self._profile_root_path()
        if profile_root is None:
            return None
        from rexecop.profile.loader import load_profile

        contract = load_profile(profile_root).connector_contract(self.connector_name)
        if contract is None:
            return None
        return validate_http_action_shape(
            connector_name=self.connector_name,
            action=action,
            connector_contract=contract,
            connector_config=self.config,
        )

    def _invoke_with_retry(
        self,
        request: ConnectorRequest,
        action_spec: dict[str, Any],
    ) -> ConnectorResponse:
        retry_cfg = resolve_retry_config(
            self.config.get("retry"),
            action_spec.get("retry"),
        )
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
            time.sleep(retry_delay_seconds(retry_cfg, attempt))
        assert last_response is not None
        return last_response

    def _invoke_once(
        self,
        request: ConnectorRequest,
        action_spec: dict[str, Any],
    ) -> ConnectorResponse:
        pagination = action_spec.get("pagination")
        if isinstance(pagination, dict) and pagination.get("items_path"):
            return self._invoke_paginated(request, action_spec, pagination)
        return self._invoke_single(request, action_spec)

    def _invoke_paginated(
        self,
        request: ConnectorRequest,
        action_spec: dict[str, Any],
        pagination: dict[str, Any],
    ) -> ConnectorResponse:
        items_path = str(pagination.get("items_path") or "").strip()
        next_path = str(pagination.get("next_path") or "").strip()
        if not items_path:
            return ConnectorResponse(
                connector=request.connector,
                action=request.action,
                success=False,
                error="pagination.items_path is required",
                data={"error_class": connector_errors.VALIDATION_FAILED},
            )
        max_pages = int(pagination.get("max_pages") or 10)
        collected: list[Any] = []
        next_url: str | None = None
        pages = 0
        while pages < max_pages:
            response, parsed, request_url = self._fetch_json(request, action_spec, next_url)
            if not response.success:
                return response
            items = get_json_path(parsed, items_path)
            if isinstance(items, list):
                collected.extend(items)
            pages += 1
            if not next_path:
                break
            next_value = get_json_path(parsed, next_path)
            next_url = resolve_next_url(self._resolve_base_url(), request_url, next_value)
            if not next_url:
                break
        payload = redact_payload(merge_paginated_items(items_path, collected))
        return ConnectorResponse(
            connector=request.connector,
            action=request.action,
            success=True,
            data=payload,
        )

    def _invoke_single(
        self,
        request: ConnectorRequest,
        action_spec: dict[str, Any],
    ) -> ConnectorResponse:
        response, parsed, _request_url = self._fetch_json(request, action_spec, None)
        if not response.success:
            return response
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
            payload = dict(parsed) if isinstance(parsed, dict) else {"value": parsed}
        return ConnectorResponse(
            connector=request.connector,
            action=request.action,
            success=True,
            data=redact_payload(payload),
        )

    def _fetch_json(
        self,
        request: ConnectorRequest,
        action_spec: dict[str, Any],
        override_url: str | None,
    ) -> tuple[ConnectorResponse, dict[str, Any], str]:
        try:
            request_url = override_url or self._build_request_url(request, action_spec)
            method = str(action_spec.get("method") or "GET").upper()
            headers = {"Accept": "application/json"}
            headers.update(self._auth_headers())
            body = action_spec.get("body")
            request_body: bytes | None = None
            if body is not None and override_url is None:
                headers["Content-Type"] = "application/json"
                request_body = json.dumps(body).encode("utf-8")
            timeout = effective_timeout_seconds(
                request,
                float(
                    action_spec.get("timeout_seconds")
                    or self.config.get("timeout_seconds")
                    or 10
                ),
            )
            req = urllib.request.Request(
                url=request_url,
                method=method,
                headers=headers,
                data=request_body,
            )
            max_response_bytes = effective_output_bytes(
                request,
                int(
                    action_spec.get("max_response_bytes")
                    or self.config.get("max_response_bytes")
                    or 65536
                ),
            )
            if max_response_bytes < 1:
                raise RExecOpValidationError("max_response_bytes must be positive")
            urlopen_kwargs: dict[str, Any] = {"timeout": timeout}
            tls_context = self._tls_context(request_url)
            if tls_context is not None:
                urlopen_kwargs["context"] = tls_context
            with urllib.request.urlopen(req, **urlopen_kwargs) as resp:
                raw_bytes = resp.read(max_response_bytes + 1)
            if len(raw_bytes) > max_response_bytes:
                return (
                    ConnectorResponse(
                        connector=request.connector,
                        action=request.action,
                        success=False,
                        error="http response exceeds max_response_bytes",
                        data={
                            "error_class": connector_errors.VALIDATION_FAILED,
                            "output_truncated": True,
                            "max_response_bytes": max_response_bytes,
                        },
                    ),
                    {},
                    request_url,
                )
            raw = raw_bytes.decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            if not isinstance(parsed, dict):
                parsed = {"value": parsed}
            return (
                ConnectorResponse(
                    connector=request.connector,
                    action=request.action,
                    success=True,
                    data={},
                ),
                parsed,
                request_url,
            )
        except urllib.error.HTTPError as exc:
            body_snippet = read_http_error_body(exc)
            error_data: dict[str, Any] = {
                "error_class": http_error_class(exc.code),
                "status_code": exc.code,
            }
            if body_snippet:
                error_data["body_snippet"] = body_snippet
            return (
                ConnectorResponse(
                    connector=request.connector,
                    action=request.action,
                    success=False,
                    error=f"http error {exc.code}",
                    data=error_data,
                ),
                {},
                override_url or "",
            )
        except TimeoutError:
            return (
                ConnectorResponse(
                    connector=request.connector,
                    action=request.action,
                    success=False,
                    error="connector timeout",
                    data={"error_class": connector_errors.TIMEOUT},
                ),
                {},
                override_url or "",
            )
        except urllib.error.URLError as exc:
            reason = str(exc.reason)
            error_class = (
                connector_errors.TIMEOUT
                if "timed out" in reason.lower()
                else connector_errors.TRANSIENT
            )
            return (
                ConnectorResponse(
                    connector=request.connector,
                    action=request.action,
                    success=False,
                    error=redact_text(reason),
                    data={"error_class": error_class},
                ),
                {},
                override_url or "",
            )
        except json.JSONDecodeError as exc:
            return (
                ConnectorResponse(
                    connector=request.connector,
                    action=request.action,
                    success=False,
                    error=f"invalid json response: {exc}",
                    data={"error_class": connector_errors.VALIDATION_FAILED},
                ),
                {},
                override_url or "",
            )
        except RExecOpValidationError as exc:
            return (
                ConnectorResponse(
                    connector=request.connector,
                    action=request.action,
                    success=False,
                    error=redact_text(str(exc)),
                    data={"error_class": connector_errors.VALIDATION_FAILED},
                ),
                {},
                override_url or "",
            )

    def _build_request_url(self, request: ConnectorRequest, action_spec: dict[str, Any]) -> str:
        base_url = self._resolve_base_url()
        path = self._render_template(str(action_spec.get("path") or "/"), request)
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        query = action_spec.get("query")
        if isinstance(query, dict) and query:
            url = f"{url}?{urlencode({str(k): str(v) for k, v in query.items()})}"
        return url

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

    def _tls_context(self, request_url: str) -> ssl.SSLContext | None:
        tls = self.config.get("tls")
        if tls is None:
            return None
        if not isinstance(tls, dict):
            raise RExecOpValidationError("http_api tls config must be a mapping")
        unknown = set(tls) - {"ca_file_secret_ref"}
        if unknown:
            raise RExecOpValidationError(
                "http_api tls config contains unsupported fields"
            )
        if not request_url.lower().startswith("https://"):
            raise RExecOpValidationError("http_api tls config requires an https base URL")
        ca_file_ref = str(tls.get("ca_file_secret_ref") or "").strip()
        if not ca_file_ref:
            raise RExecOpValidationError("http_api tls.ca_file_secret_ref is required")
        ca_file = self.secret_resolver.resolve(ca_file_ref)
        try:
            return ssl.create_default_context(cafile=ca_file)
        except (OSError, ssl.SSLError) as exc:
            raise RExecOpValidationError(
                "http_api TLS CA file could not be loaded"
            ) from exc

    def _auth_headers(self) -> dict[str, str]:
        auth = self.config.get("auth")
        if not isinstance(auth, dict):
            return {}
        secret_ref = str(auth.get("secret_ref") or "").strip()
        if not secret_ref:
            return {}
        value = self.secret_resolver.resolve(secret_ref)
        register_secret_value(value)
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
