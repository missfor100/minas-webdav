"""Probe Xiaomi NAS WebDAV capability surface (read + write round-trip).

Usage: python -m minas_webdav probe
"""
from __future__ import annotations

import base64
import http.client
import os
import re
import socket
import ssl
import sys
import urllib.parse
from pathlib import Path

from . import config as _mconf


def enc(path: str) -> str:
    return "/".join(urllib.parse.quote(p, safe="") if p else p for p in path.split("/"))


def run(argv: list[str] | None = None) -> int:
    cfg = _mconf.apply_env()
    cn = cfg.get("cn") or os.environ.get("MINAS_CN", "")
    host, port = os.environ["MINAS_IP"], int(os.environ["MINAS_PORT"])
    user, password = os.environ.get("MINAS_USER", ""), os.environ.get("MINAS_PASS", "")
    if not user or not password:
        print("missing credentials (MINAS_USER/MINAS_PASS)", file=sys.stderr)
        return 3
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    cert_home = Path(
        os.environ.get("MINAS_CERT_HOME")
        or (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "minasCert")
    )
    certs = sorted(cert_home.glob("*_cert.pem"), key=lambda x: x.stat().st_mtime, reverse=True)
    if not certs:
        print(f"no client certs under {cert_home}; login official client first", file=sys.stderr)
        return 3
    cert = certs[0]
    key = cert_home / (certs[0].name.replace("_cert.pem", "_private_key.pem"))
    ca = cert_home / "ca_chain.pem"

    def req(method, path, body=None, headers=None, depth=None):
        headers = dict(headers or {})
        headers.setdefault("Authorization", "Basic " + token)
        headers.setdefault("Host", cn)
        headers.setdefault("Connection", "close")
        if depth is not None:
            headers["Depth"] = str(depth)
        ctx = ssl._create_unverified_context()
        sock = socket.create_connection((host, port), timeout=20)
        ssock = ctx.wrap_socket(sock, server_hostname=cn)
        conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=20)
        conn.sock = ssock
        if body is not None and "Content-Length" not in headers:
            headers["Content-Length"] = str(len(body))
        conn.request(method, enc(path), body=body, headers=headers)
        r = conn.getresponse()
        data = r.read()
        hdrs = dict(r.getheaders())
        conn.close()
        return r.status, hdrs, data

    def show(title, st, hdrs, data, n=400):
        print(f"\n== {title} -> {st} ==")
        interesting = {
            k: v
            for k, v in hdrs.items()
            if k.lower()
            in ("dav", "ms-author-via", "allow", "content-type", "content-length",
                "location", "www-authenticate", "x-request-id")
        }
        if interesting:
            print("  hdrs:", interesting)
        text = data.decode("utf-8", "replace")
        print(" ", text[:n].replace("\n", " ") if text else "(empty)")

    root = os.environ.get("MINAS_DATA_ROOT", "/pool0/data")
    # unique per run: locks on a path outlive file deletion (Timeout Second-60)
    ts = str(int(__import__("time").time()))
    probe_file = f"{root}/_webdav_probe_{os.getpid()}_{ts}.txt"
    probe_dir = f"{root}/_probe_dir_{os.getpid()}_{ts}/"
    moved_path = probe_dir + "moved.txt"
    copied_path = probe_dir + "copied.txt"

    # 1) OPTIONS
    st, h, d = req("OPTIONS", root + "/")
    show(f"OPTIONS {root}/", st, h, d)

    # 2) PROPFIND depth 0 / 1 / infinity
    propfind_body = (
        b'<?xml version="1.0"?><a:propfind xmlns:a="DAV:"><a:prop>'
        b"<a:resourcetype/><a:getcontentlength/><a:getlastmodified/><a:displayname/>"
        b"</a:prop></a:propfind>"
    )
    for dep in ("0", "1", "infinity"):
        st, h, d = req(
            "PROPFIND", root + "/",
            body=propfind_body,
            headers={"Content-Type": "application/xml"},
            depth=dep,
        )
        show(f"PROPFIND depth={dep}", st, h, d, n=250)

    # 3) GET file (expected 404 unless present) / directory HTML index
    st, h, d = req("GET", probe_file)
    show("GET probe file (pre-create)", st, h, d)
    st, h, d = req("GET", root + "/")
    show("GET dir (HTML index)", st, h, d)

    # 4) PUT create / overwrite / MKCOL / MOVE / COPY / DELETE
    st, h, d = req("PUT", probe_file, body=b"probe-write-ok\n", headers={"Content-Type": "text/plain"})
    show("PUT create", st, h, d, n=80)
    st, h, d = req("GET", probe_file)
    show("GET after PUT", st, h, d, n=80)
    st, h, d = req("PUT", probe_file, body=b"overwritten\n", headers={"Content-Type": "text/plain"})
    show("PUT overwrite", st, h, d, n=80)
    st, h, d = req("MKCOL", probe_dir)
    show("MKCOL", st, h, d, n=80)
    # MOVE/COPY: path-only Destination (IP-in-URL returns 400 on this device)
    st, h, d = req(
        "MOVE", probe_file,
        headers={"Destination": enc(moved_path), "Overwrite": "T"},
    )
    show("MOVE (path-only Destination)", st, h, d, n=120)
    st, h, d = req(
        "COPY", moved_path,
        headers={"Destination": enc(copied_path)},
    )
    show("COPY (path-only Destination)", st, h, d, n=80)
    # also record what an IP-URL Destination does, for reference
    st, h, d = req(
        "COPY", moved_path,
        headers={"Destination": f"https://{host}:{port}{probe_dir}ip_dst.txt"},
    )
    show("COPY (IP-URL Destination, expect 400)", st, h, d, n=80)

    # 5) Range GET
    st, h, d = req("GET", moved_path, headers={"Range": "bytes=0-1"})
    show("GET Range 0-1", st, h, d, n=80)

    # 6) LOCK / UNLOCK
    lock_body = (
        b'<?xml version="1.0"?><d:lockinfo xmlns:d="DAV:">'
        b"<d:lockscope><d:exclusive/></d:lockscope>"
        b'<d:locktype><d:write/></d:locktype><d:owner>agent</d:owner></d:lockinfo>'
    )
    st, h, d = req(
        "LOCK", moved_path,
        body=lock_body,
        headers={"Content-Type": "application/xml", "Timeout": "Second-60"},
    )
    show("LOCK", st, h, d, n=200)
    if st in (200, 201):
        text = d.decode("utf-8", "replace")
        # RFC 4918: <D:locktoken><D:href>opaquelocktoken:...</D:href></D:locktoken>
        m = re.search(r"<[Dd]:locktoken>\s*<[Dd]:href>(.*?)</[Dd]:href>", text)
        if m:
            st, h, d = req(
                "UNLOCK", moved_path,
                headers={"Lock-Token": "<" + m.group(1).strip() + ">"},
            )
            show("UNLOCK", st, h, d, n=80)
        else:
            print("\n== UNLOCK skipped (no locktoken in LOCK response) ==")

    # 7) cleanup
    st, h, d = req("DELETE", copied_path)
    show("DELETE copied", st, h, d, n=80)
    st, h, d = req("DELETE", moved_path)
    show("DELETE moved", st, h, d, n=80)
    st, h, d = req("DELETE", probe_dir)
    show("DELETE dir", st, h, d, n=80)
    st, h, d = req("DELETE", probe_file)
    show("DELETE leftover", st, h, d, n=80)

    # 8) auth recap
    print("\n== auth recap ==")
    for label, use_auth in (("Basic only", True), ("no auth", False)):
        try:
            headers = {"Host": cn, "Connection": "close"}
            if use_auth:
                headers["Authorization"] = "Basic " + token
            ctx = ssl._create_unverified_context()
            sock = socket.create_connection((host, port), timeout=10)
            ssock = ctx.wrap_socket(sock, server_hostname=cn)
            c = http.client.HTTPSConnection(host, port, context=ctx, timeout=10)
            c.sock = ssock
            c.request(
                "PROPFIND", root + "/",
                body=propfind_body,
                headers={**headers, "Depth": "0", "Content-Type": "application/xml"},
            )
            r = c.getresponse()
            r.read()
            print(f"  {label}: {r.status}")
            c.close()
        except Exception as e:
            print(f"  {label}: FAIL {e}")

    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(run())
