from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


class StagingHttpServer:
    """Minimal Proxmox/PBS API stub for Phase 9 staging tests."""

    def __init__(self) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.restart_calls = 0
        self.transient_failures_remaining = 0

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("server not started")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def start(self) -> None:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path
                query = parse_qs(parsed.query)

                if path == "/proxmox/vms":
                    parent._json(
                        self,
                        {
                            "vms": [
                                {"id": "vm-101", "name": "zabbix-proxy", "critical": True},
                                {"id": "vm-102", "name": "backup-gateway", "critical": True},
                            ]
                        },
                    )
                    return
                if path == "/proxmox/vms/paged":
                    page = int((query.get("page") or ["1"])[0])
                    if page == 1:
                        parent._json(
                            self,
                            {
                                "data": {
                                    "vms": [{"id": "vm-101", "name": "zabbix-proxy"}],
                                    "next": "/proxmox/vms/paged?page=2",
                                }
                            },
                        )
                        return
                    if page == 2:
                        parent._json(
                            self,
                            {
                                "data": {
                                    "vms": [{"id": "vm-102", "name": "backup-gateway"}],
                                }
                            },
                        )
                        return
                if path == "/pbs/snapshots":
                    parent._json(
                        self,
                        {
                            "snapshots": [
                                {"vm_id": "vm-101", "status": "ok"},
                                {"vm_id": "vm-102", "status": "ok"},
                            ]
                        },
                    )
                    return
                if path == "/proxmox/transient":
                    if parent.transient_failures_remaining > 0:
                        parent.transient_failures_remaining -= 1
                        self.send_error(503, "temporary unavailable")
                        return
                    parent._json(self, {"status": "ok"})
                    return
                if path == "/proxmox/auth-error":
                    self.send_response(401)
                    body = json.dumps(
                        {"message": "invalid token", "api_key": "secret-token"}
                    ).encode("utf-8")
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                if self.path == "/proxmox/restart":
                    parent.restart_calls += 1
                    parent._json(
                        self,
                        {
                            "before_state": {
                                "vm_id": "vm-101",
                                "agent_status": "running",
                            },
                            "after_state": {
                                "vm_id": "vm-101",
                                "agent_status": "restarted",
                            },
                            "mutation": "restart_zabbix_agent",
                        },
                    )
                    return
                self.send_error(404)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def _json(self, handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
