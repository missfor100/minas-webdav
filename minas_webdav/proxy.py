"""Local WebDAV reverse proxy for Xiaomi Smart Storage (MINAS).
http://127.0.0.1:8899 -> https://<nas>:5000 with Basic auth.
Tuned for Windows WebClient (net use / Explorer).
"""
from __future__ import annotations

import base64
import http.client
import os
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

from . import config as _mconf

UP_HOST = ""
UP_PORT = 5000
UP_USER = ""
UP_PASS = ""
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8899
UP_CN = ""
TOKEN = ""
_ctx = ssl._create_unverified_context()


def _init() -> None:
    global UP_HOST, UP_PORT, UP_USER, UP_PASS, LISTEN_HOST, LISTEN_PORT, UP_CN, TOKEN
    cfg = _mconf.apply_env()
    UP_HOST = cfg["host"]
    UP_PORT = int(cfg["port"])
    UP_USER = os.environ.get("MINAS_USER", "")
    UP_PASS = os.environ.get("MINAS_PASS", "")
    LISTEN_HOST = cfg.get("proxy", {}).get("listen_host", "127.0.0.1")
    LISTEN_PORT = int(cfg.get("proxy", {}).get("listen_port", 8899))
    UP_CN = cfg.get("cn") or os.environ.get("MINAS_CN", "")
    TOKEN = base64.b64encode(f"{UP_USER}:{UP_PASS}".encode()).decode()

HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length", "host",
}


def ascii_path(path: str) -> str:
    """Keep already-encoded paths; encode raw non-ascii for upstream."""
    try:
        path.encode("ascii")
        return path
    except UnicodeEncodeError:
        parts = path.split("/")
        return "/".join(quote(p, safe="") if p else p for p in parts)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))
        sys.stderr.flush()

    def _proxy(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        up_path = ascii_path(self.path)
        conn = http.client.HTTPSConnection(UP_HOST, UP_PORT, context=_ctx, timeout=120)
        headers = {}
        for k, v in self.headers.items():
            if k.lower() in HOP:
                continue
            headers[k] = v
        headers["Authorization"] = "Basic " + TOKEN
        headers["Host"] = UP_CN
        headers["Connection"] = "close"
        if body:
            headers["Content-Length"] = str(len(body))
        try:
            conn.request(method, up_path, body=body if body else None, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            status = resp.status
            # Windows WebClient prefers 200 over 204 for DELETE/LOCK
            if method in ("DELETE", "MKCOL", "UNLOCK") and status == 204:
                status = 200
            self.close_connection = True
            self.send_response(status, resp.reason if status != 200 else "OK")
            skip = set(HOP)
            for k, v in resp.getheaders():
                if k.lower() in skip:
                    continue
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data) if status != 200 else 0))
            self.send_header("Connection", "close")
            self.send_header("MS-Author-Via", "DAV")
            self.end_headers()
            if method != "HEAD" and status != 200 and data:
                self.wfile.write(data)
            elif method != "HEAD" and method not in ("DELETE", "MKCOL", "UNLOCK") and data:
                self.wfile.write(data)
        except Exception as e:
            msg = f"{type(e).__name__}: {e}".encode()
            self.close_connection = True
            self.send_response(502)
            self.send_header("Content-Length", str(len(msg)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(msg)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def do_GET(self): self._proxy("GET")
    def do_HEAD(self): self._proxy("HEAD")
    def do_PUT(self): self._proxy("PUT")
    def do_POST(self): self._proxy("POST")
    def do_DELETE(self): self._proxy("DELETE")
    def do_OPTIONS(self): self._proxy("OPTIONS")
    def do_PROPFIND(self): self._proxy("PROPFIND")
    def do_PROPPATCH(self): self._proxy("PROPPATCH")
    def do_MKCOL(self): self._proxy("MKCOL")
    def do_COPY(self): self._proxy("COPY")
    def do_MOVE(self): self._proxy("MOVE")
    def do_LOCK(self): self._proxy("LOCK")
    def do_UNLOCK(self): self._proxy("UNLOCK")


def run() -> int:
    _init()
    httpd = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    httpd.daemon_threads = True
    print(f"proxy http://{LISTEN_HOST}:{LISTEN_PORT} -> https://{UP_HOST}:{UP_PORT}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(run())
