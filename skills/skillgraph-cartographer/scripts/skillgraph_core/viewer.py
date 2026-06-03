"""Local HTML viewer rendering and HTTP serving."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Any
import webbrowser


HTML_TEMPLATE = Path(__file__).with_name("viewer.html").read_text(encoding="utf-8")


def html_for_graph(graph: dict[str, Any] | None = None) -> str:
    graph_json = "null" if graph is None else json.dumps(graph, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__GRAPH_JSON__", graph_json.replace("</", "<\\/"))


def make_viewer_handler(graph: dict[str, Any]) -> type[BaseHTTPRequestHandler]:
    html_text = html_for_graph().encode("utf-8")
    graph_json = (json.dumps(graph, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    class ViewerHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path in {"/", "/index.html"}:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_text)))
                self.end_headers()
                self.wfile.write(html_text)
                return
            if self.path == "/graph.json":
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(graph_json)))
                self.end_headers()
                self.wfile.write(graph_json)
                return
            self.send_error(404)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ViewerHandler


def serve_viewer(graph: dict[str, Any], host: str, port: int, open_browser: bool) -> int:
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
