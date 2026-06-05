"""Local HTML viewer rendering and HTTP serving."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
from typing import Any
import webbrowser


HTML_TEMPLATE = Path(__file__).with_name("viewer.html").read_text(encoding="utf-8")
LEGACY_RELATION_WEIGHT_KEYS = ("confidence", "confidenceScore")


def viewer_graph(graph: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(graph))
    for key in ("edges", "inferredEdges"):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            for legacy_key in LEGACY_RELATION_WEIGHT_KEYS:
                value.pop(legacy_key, None)
    return payload


def html_for_graph(graph: dict[str, Any] | None = None) -> str:
    graph_json = "null" if graph is None else json.dumps(viewer_graph(graph), ensure_ascii=False)
    return HTML_TEMPLATE.replace("__GRAPH_JSON__", graph_json.replace("</", "<\\/"))


def make_viewer_handler(graph: dict[str, Any]) -> type[BaseHTTPRequestHandler]:
    html_text = html_for_graph().encode("utf-8")
    graph_json = (json.dumps(viewer_graph(graph), ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    class ViewerHandler(BaseHTTPRequestHandler):
        def send_viewer_headers(self, content_type: str, content_length: int) -> None:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(content_length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path in {"/", "/index.html"}:
                self.send_response(200)
                self.send_viewer_headers("text/html; charset=utf-8", len(html_text))
                self.end_headers()
                self.wfile.write(html_text)
                return
            if self.path == "/graph.json":
                self.send_response(200)
                self.send_viewer_headers("application/json; charset=utf-8", len(graph_json))
                self.end_headers()
                self.wfile.write(graph_json)
                return
            if self.path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            self.send_error(404)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ViewerHandler


def is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def serve_viewer(graph: dict[str, Any], host: str, port: int, open_browser: bool, *, allow_non_loopback: bool = False) -> int:
    if not is_loopback_host(host) and not allow_non_loopback:
        raise SystemExit(f"refusing to bind non-loopback host {host}; pass --allow-non-loopback to expose graph JSON")
    server = ThreadingHTTPServer((host, port), make_viewer_handler(graph))
    url = f"http://{server.server_address[0]}:{server.server_address[1]}/"
    print(url, flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0
