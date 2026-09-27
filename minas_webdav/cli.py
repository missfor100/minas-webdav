#!/usr/bin/env python3
"""MINAS WebDAV CLI for agent file ops on Xiaomi Smart Storage.

No drive-letter mount required. Talks to the device WebDAV API directly.

Examples:
  python -m minas_webdav ls /pool0/data
  python -m minas_webdav ls /pool0/data --json
  python -m minas_webdav du /pool0/data/我的照片 --max-depth 2
  python -m minas_webdav get /pool0/data/test.txt ./local.txt
  python -m minas_webdav put ./local.txt /pool0/data/test.txt
  python -m minas_webdav mkdir /pool0/data/work
  python -m minas_webdav mv /pool0/data/a.txt /pool0/data/work/a.txt
  python -m minas_webdav rm /pool0/data/work --recursive
  python -m minas_webdav df
  python -m minas_webdav find /pool0/data --name "*.mp4" --max-depth 3

Exit codes: 0 ok, 1 usage/error, 2 not found, 3 auth/network.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import ssl
import sys
import http.client
import urllib.parse
from pathlib import Path
from typing import Any, Iterable

from . import config as _mconf
_mconf.apply_env()

DEFAULT_NAS_IP = os.environ.get("MINAS_IP", "192.168.1.100")
DEFAULT_WEBDAV_PORT = int(os.environ.get("MINAS_PORT", "5000"))
CERT_HOME = Path(os.environ.get("MINAS_CERT_HOME", Path(os.environ.get("LOCALAPPDATA", "")) / "minasCert"))
DATA_ROOT = os.environ.get("MINAS_DATA_ROOT", "/pool0/data")


def enc_path(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return "/".join(urllib.parse.quote(seg, safe="") if seg else seg for seg in path.split("/"))


def norm(path: str) -> str:
    path = path.replace("\\", "/")
    if not path.startswith("/"):
        path = "/" + path
    # collapse //
    while "//" in path:
        path = path.replace("//", "/")
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return path


def find_certs() -> tuple[str, str, str] | None:
    if not CERT_HOME.exists():
        return None
    certs = sorted(CERT_HOME.glob("*_cert.pem"), key=lambda p: p.stat().st_mtime, reverse=True)
    for cert in certs:
        uid_did = cert.name.replace("_cert.pem", "")
        key = CERT_HOME / f"{uid_did}_private_key.pem"
        ca = CERT_HOME / "ca_chain.pem"
        if key.exists() and ca.exists():
            return str(cert), str(key), str(ca)
    return None


class MinasWebDAV:
    def __init__(self, ip: str = DEFAULT_NAS_IP, port: int = DEFAULT_WEBDAV_PORT):
        self.ip = ip
        self.port = port
        self.username = os.environ.get("MINAS_USER", "")
        self.password = os.environ.get("MINAS_PASS", "")
        self.server_cn = os.environ.get("MINAS_CN", "")
        self._ctx = None
        self._token = ""
        self._connected = False

    def _make_ctx(self, with_mtls: bool) -> ssl.SSLContext:
        certs = find_certs()
        if with_mtls and certs:
            cert, key, ca = certs
            ctx = ssl.create_default_context(cafile=ca)
            ctx.load_cert_chain(certfile=cert, keyfile=key)
            ctx.check_hostname = False
            return ctx
        ctx = ssl._create_unverified_context()
        return ctx

    def _resolve_cn(self) -> str:
        if self.server_cn:
            return self.server_cn
        # probe server cert CN
        ctx = ssl._create_unverified_context()
        sock = socket.create_connection((self.ip, 443 if self.port == 5000 else self.port), timeout=8)
        try:
            ssock = ctx.wrap_socket(sock, server_hostname="x")
            der = ssock.getpeercert(binary_form=True)
            ssock.close()
        except Exception:
            # webdav port may not do 443 handshake the same; try webdav port
            sock.close()
            sock = socket.create_connection((self.ip, self.port), timeout=8)
            ssock = ctx.wrap_socket(sock, server_hostname="x")
            der = ssock.getpeercert(binary_form=True)
            ssock.close()
        # parse CN via certutil if available
        import subprocess
        import tempfile
        tmp = Path(tempfile.gettempdir()) / "minas_peer.cer"
        tmp.write_bytes(der)
        out = subprocess.check_output(["certutil", "-dump", str(tmp)], stderr=subprocess.STDOUT)
        text = out.decode("utf-8", "replace")
        m = re.search(r"CN=([^,\r\n]+)", text)
        if not m:
            raise RuntimeError("cannot parse server CN from cert")
        self.server_cn = m.group(1).strip()
        return self.server_cn

    def discover(self) -> dict[str, Any]:
        """Fetch pool info via LuCI mTLS API (local tunnel or direct)."""
        certs = find_certs()
        if not certs:
            raise RuntimeError(f"certs not found under {CERT_HOME}; login official client first")
        cert, key, ca = certs
        cn = self._resolve_cn()
        ctx = ssl.create_default_context(cafile=ca)
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        ctx.check_hostname = False

        hosts = [(self.ip, 443), ("127.0.0.1", 51087)]
        last_err = None
        for host, port in hosts:
            try:
                conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=10)
                sock = socket.create_connection((host, port), timeout=10)
                ssock = ctx.wrap_socket(sock, server_hostname=cn)
                conn.sock = ssock
                body = b"{}"
                conn.request(
                    "POST",
                    "/cgi-bin/luci/filemgr/get_pool_info",
                    body=body,
                    headers={"Content-Type": "application/json", "Host": cn, "Content-Length": str(len(body))},
                )
                resp = conn.getresponse()
                data = resp.read()
                conn.close()
                if resp.status != 200:
                    last_err = f"{host}:{port} HTTP {resp.status}"
                    continue
                payload = json.loads(data.decode("utf-8", "replace"))
                w = payload.get("data", {}).get("webDAV") or {}
                if not w.get("username"):
                    last_err = f"{host}:{port} no webDAV creds"
                    continue
                self.username = w["username"]
                self.password = w["password"]
                self.server_cn = cn
                self._connected = True
                return payload
            except Exception as e:
                last_err = f"{host}:{port} {type(e).__name__}: {e}"
        raise RuntimeError(f"discover failed: {last_err}")

    def ensure_auth(self) -> None:
        if self.username and self.password and self.server_cn:
            self._token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            self._connected = True
            return
        if not self._connected:
            self.discover()
            self._token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        with_mtls: bool = False,
    ) -> tuple[int, dict[str, str], bytes]:
        self.ensure_auth()
        headers = dict(headers or {})
        headers.setdefault("Authorization", "Basic " + self._token)
        headers.setdefault("Host", self.server_cn)
        headers.setdefault("Connection", "close")
        headers.setdefault("User-Agent", "minas-cli/1.0")
        if body is not None:
            headers["Content-Length"] = str(len(body))
        ctx = self._make_ctx(with_mtls=with_mtls)
        sock = socket.create_connection((self.ip, self.port), timeout=60)
        ssock = ctx.wrap_socket(sock, server_hostname=self.server_cn)
        conn = http.client.HTTPSConnection(self.ip, self.port, context=ctx, timeout=60)
        conn.sock = ssock
        try:
            conn.request(method, enc_path(path), body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            hdrs = {k: v for k, v in resp.getheaders()}
            return resp.status, hdrs, data
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def propfind(self, path: str, depth: str = "1", with_mtls: bool = False) -> list[dict[str, Any]]:
        body = (
            b'<?xml version="1.0"?>'
            b'<a:propfind xmlns:a="DAV:"><a:prop>'
            b"<a:displayname/><a:getcontentlength/><a:resourcetype/>"
            b"<a:getlastmodified/><a:creationdate/>"
            b"</a:prop></a:propfind>"
        )
        status, _, data = self.request(
            "PROPFIND",
            path,
            body=body,
            headers={"Content-Type": "application/xml", "Depth": depth},
            with_mtls=with_mtls,
        )
        if status not in (207, 200):
            raise RuntimeError(f"PROPFIND {path} -> HTTP {status}: {data[:200]!r}")
        return parse_multistatus(data.decode("utf-8", "replace"), self_prefix=path)

    def is_dir(self, path: str) -> bool:
        items = self.propfind(path, depth="0", with_mtls=True)
        return bool(items) and items[0]["is_dir"]


def parse_multistatus(text: str, self_prefix: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for block in text.split("<D:response>")[1:]:
        href_m = re.search(r"<D:href>(.*?)</D:href>", block)
        if not href_m:
            continue
        href = urllib.parse.unquote(href_m.group(1))
        name_m = re.search(r"<D:displayname>(.*?)</D:displayname>", block)
        size_m = re.search(r"<D:getcontentlength>(.*?)</D:getcontentlength>", block)
        mod_m = re.search(r"<D:getlastmodified>(.*?)</D:getlastmodified>", block)
        is_dir = "<D:collection/>" in block
        name = urllib.parse.unquote(name_m.group(1)) if name_m else href.rstrip("/").split("/")[-1]
        size = int(size_m.group(1)) if size_m and size_m.group(1).isdigit() else 0
        # skip self entry when depth>0 includes it
        if self_prefix and href.rstrip("/") == self_prefix.rstrip("/") and len(text.split("<D:response>")) > 2:
            # keep only if sole entry (depth 0)
            continue
        items.append(
            {
                "path": href if href.startswith("/") else "/" + href,
                "name": name,
                "size": size,
                "is_dir": is_dir,
                "modified": mod_m.group(1) if mod_m else "",
            }
        )
    # depth=0 often returns exactly one self item — keep it
    if not items:
        items = parse_multistatus_self(text)
    return items


def parse_multistatus_self(text: str) -> list[dict[str, Any]]:
    out = []
    for block in text.split("<D:response>")[1:]:
        href_m = re.search(r"<D:href>(.*?)</D:href>", block)
        if not href_m:
            continue
        href = urllib.parse.unquote(href_m.group(1))
        name_m = re.search(r"<D:displayname>(.*?)</D:displayname>", block)
        is_dir = "<D:collection/>" in block
        size_m = re.search(r"<D:getcontentlength>(.*?)</D:getcontentlength>", block)
        out.append(
            {
                "path": href,
                "name": urllib.parse.unquote(name_m.group(1)) if name_m else href,
                "size": int(size_m.group(1)) if size_m and size_m.group(1).isdigit() else 0,
                "is_dir": is_dir,
                "modified": "",
            }
        )
    return out


def fmt_size(n: float) -> str:
    n = float(n)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def ensure_data_path(path: str) -> str:
    p = norm(path)
    if p in ("", "/"):
        return DATA_ROOT
    if not p.startswith("/pool0"):
        # allow bare names relative to data root
        if not p.startswith("/"):
            p = "/" + p
        if not p.startswith("/pool0"):
            p = DATA_ROOT.rstrip("/") + p
    return p


# --------------- commands ---------------

def cmd_ls(client: MinasWebDAV, args: argparse.Namespace) -> int:
    path = ensure_data_path(args.path)
    depth = "0" if args.long is False and args.self_only else "1"
    if args.self_only:
        depth = "0"
    try:
        items = client.propfind(path, depth="0" if args.self_only else "1", with_mtls=True)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 2
    # For depth 1, drop self if present
    if not args.self_only:
        items = [it for it in items if norm(it["path"]) != norm(path)]
    items.sort(key=lambda x: (not x["is_dir"], x["name"]))
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0
    for it in items:
        kind = "d" if it["is_dir"] else "-"
        size = fmt_size(it["size"]) if not it["is_dir"] else ""
        print(f"{kind} {size:>10}  {it['name']}")
    return 0


def cmd_stat(client: MinasWebDAV, args: argparse.Namespace) -> int:
    path = ensure_data_path(args.path)
    try:
        items = client.propfind(path, depth="0", with_mtls=True)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 2
    if not items:
        print("not found", file=sys.stderr)
        return 2
    it = items[0]
    if args.json:
        print(json.dumps(it, ensure_ascii=False, indent=2))
    else:
        print(f"path: {it['path']}")
        print(f"name: {it['name']}")
        print(f"type: {'dir' if it['is_dir'] else 'file'}")
        print(f"size: {it['size']} ({fmt_size(it['size'])})")
        print(f"modified: {it.get('modified','')}")
    return 0


def walk_sizes(client: MinasWebDAV, path: str, max_depth: int, depth: int = 0) -> tuple[int, int]:
    """Return (total_bytes, file_count)."""
    total = 0
    count = 0
    try:
        items = client.propfind(path, depth="1", with_mtls=True)
    except Exception as e:
        print(f"warn: {path}: {e}", file=sys.stderr)
        return 0, 0
    for it in items:
        if norm(it["path"]) == norm(path):
            continue
        if it["is_dir"]:
            if depth < max_depth:
                sub_total, sub_count = walk_sizes(client, it["path"], max_depth, depth + 1)
                total += sub_total
                count += sub_count
        else:
            total += it["size"]
            count += 1
    return total, count


def cmd_du(client: MinasWebDAV, args: argparse.Namespace) -> int:
    path = ensure_data_path(args.path)
    max_depth = args.max_depth
    try:
        items = client.propfind(path, depth="1", with_mtls=True)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 2
    rows = []
    for it in items:
        if norm(it["path"]) == norm(path):
            continue
        if it["is_dir"]:
            total, count = walk_sizes(client, it["path"], max_depth=max_depth)
            rows.append((it["name"], total, count, True))
        else:
            rows.append((it["name"], it["size"], 1, False))
    rows.sort(key=lambda r: -r[1])
    if args.json:
        print(json.dumps([{"name": n, "bytes": b, "files": c, "is_dir": d} for n, b, c, d in rows], ensure_ascii=False, indent=2))
        return 0
    grand = 0
    for name, total, count, is_dir in rows:
        grand += total
        mark = "/" if is_dir else ""
        print(f"{fmt_size(total):>10}  {count:>6}  {name}{mark}")
    print("-" * 40)
    print(f"{fmt_size(grand):>10}  TOTAL")
    return 0


def cmd_df(client: MinasWebDAV, args: argparse.Namespace) -> int:
    # use LuCI storage API when possible via discover payload
    try:
        payload = client.discover()
        pool = (payload.get("data") or {}).get("internal_pool") or []
        if pool:
            p0 = pool[0]
            total = int(p0.get("total_size") or 0)
            used = int(p0.get("used_size") or 0)
            info = {
                "name": p0.get("name"),
                "total_bytes": total,
                "used_bytes": used,
                "free_bytes": total - used,
                "total_human": fmt_size(total),
                "used_human": fmt_size(used),
                "free_human": fmt_size(total - used),
                "data_dir": p0.get("data_dir"),
            }
            if args.json:
                print(json.dumps(info, ensure_ascii=False, indent=2))
            else:
                print(f"pool: {info['name']}  data_dir={info['data_dir']}")
                print(f"total: {info['total_human']}  used: {info['used_human']}  free: {info['free_human']}")
            return 0
    except Exception as e:
        print(f"warn: pool info unavailable: {e}", file=sys.stderr)
    print("pool info unavailable", file=sys.stderr)
    return 3


def cmd_get(client: MinasWebDAV, args: argparse.Namespace) -> int:
    remote = ensure_data_path(args.remote)
    local = Path(args.local)
    status, headers, data = client.request("GET", remote, with_mtls=True)
    if status == 404:
        print(f"not found: {remote}", file=sys.stderr)
        return 2
    if status not in (200, 206):
        print(f"GET {remote} -> HTTP {status}", file=sys.stderr)
        return 1
    if local.is_dir():
        local = local / norm(remote).rstrip("/").split("/")[-1]
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(data)
    print(f"saved {local} ({fmt_size(len(data))})")
    return 0


def cmd_put(client: MinasWebDAV, args: argparse.Namespace) -> int:
    local = Path(args.local)
    if not local.exists():
        print(f"local not found: {local}", file=sys.stderr)
        return 2
    if local.is_dir():
        print("local is a directory; upload files one by one or use a sync tool", file=sys.stderr)
        return 1
    remote = ensure_data_path(args.remote)
    data = local.read_bytes()
    status, _, body = client.request(
        "PUT",
        remote,
        body=data,
        headers={"Content-Type": "application/octet-stream"},
        with_mtls=True,
    )
    if status in (200, 201, 204):
        print(f"uploaded {local} -> {remote} ({fmt_size(len(data))})")
        return 0
    print(f"PUT failed HTTP {status}: {body[:200]!r}", file=sys.stderr)
    return 1


def cmd_cat(client: MinasWebDAV, args: argparse.Namespace) -> int:
    remote = ensure_data_path(args.path)
    status, _, data = client.request("GET", remote, with_mtls=True)
    if status == 404:
        print(f"not found: {remote}", file=sys.stderr)
        return 2
    if status not in (200, 206):
        print(f"GET failed HTTP {status}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(data)
    return 0


def cmd_mkdir(client: MinasWebDAV, args: argparse.Namespace) -> int:
    path = ensure_data_path(args.path)
    # create parents
    parts = norm(path).strip("/").split("/")
    cur = ""
    last_status, last_body = 0, b""
    for part in parts:
        cur += "/" + part
        status, _, body = client.request("MKCOL", cur + "/", with_mtls=True)
        last_status, last_body = status, body
        if status not in (201, 204, 405):  # 405 = already exists
            if status != 201 and status != 204:
                # 301/405 ok-ish
                if status not in (301, 405):
                    print(f"MKCOL {cur} -> HTTP {status}", file=sys.stderr)
                    return 1
    print(f"mkdir ok: {path}")
    return 0


def cmd_rm(client: MinasWebDAV, args: argparse.Namespace) -> int:
    path = ensure_data_path(args.path)
    if args.recursive:
        # delete children first
        try:
            items = client.propfind(path, depth="1", with_mtls=True)
        except RuntimeError:
            items = []
        for it in items:
            if norm(it["path"]) == norm(path):
                continue
            if it["is_dir"]:
                cmd_rm(client, argparse.Namespace(path=it["path"], recursive=True, json=False, force=True))
            else:
                client.request("DELETE", it["path"], with_mtls=True)
    status, _, body = client.request("DELETE", path, with_mtls=True)
    if status in (200, 204, 404):
        print(f"removed: {path}")
        return 0
    if status == 409 and args.recursive:
        print(f"DELETE {path} -> 409 (not empty?)", file=sys.stderr)
        return 1
    print(f"DELETE {path} -> HTTP {status}: {body[:160]!r}", file=sys.stderr)
    return 1


def dest_header(dest_path: str, client: MinasWebDAV) -> str:
    # path-only works (IP-in-URL → 400); must be percent-encoded — header
    # values are latin-1 to http.client and raw CJK would raise.
    return enc_path(norm(dest_path))


def cmd_mv(client: MinasWebDAV, args: argparse.Namespace) -> int:
    src = ensure_data_path(args.src)
    dst = ensure_data_path(args.dst)
    status, _, body = client.request(
        "MOVE",
        src,
        headers={"Destination": dest_header(dst, client), "Overwrite": "T" if args.force else "F"},
        with_mtls=True,
    )
    if status in (200, 201, 204):
        print(f"moved {src} -> {dst}")
        return 0
    print(f"MOVE failed HTTP {status}: {body[:160]!r}", file=sys.stderr)
    return 1


def cmd_cp(client: MinasWebDAV, args: argparse.Namespace) -> int:
    src = ensure_data_path(args.src)
    dst = ensure_data_path(args.dst)
    status, _, body = client.request(
        "COPY",
        src,
        headers={"Destination": dest_header(dst, client), "Overwrite": "T" if args.force else "F"},
        with_mtls=True,
    )
    if status in (200, 201, 204):
        print(f"copied {src} -> {dst}")
        return 0
    print(f"COPY failed HTTP {status}: {body[:160]!r}", file=sys.stderr)
    return 1


def cmd_find(client: MinasWebDAV, args: argparse.Namespace) -> int:
    root = ensure_data_path(args.path)
    max_depth = args.max_depth
    name_re = re.compile(fnmatch_to_re(args.name)) if args.name else None
    found = []

    def rec(path: str, depth: int) -> None:
        try:
            items = client.propfind(path, depth="1", with_mtls=True)
        except Exception as e:
            print(f"warn: {path}: {e}", file=sys.stderr)
            return
        for it in items:
            if norm(it["path"]) == norm(path):
                continue
            ok = True
            if name_re and not name_re.search(it["name"]):
                ok = False
            if args.files and it["is_dir"]:
                ok = False
            if args.dirs and not it["is_dir"]:
                ok = False
            if ok:
                found.append(it)
            if it["is_dir"] and depth < max_depth:
                rec(it["path"], depth + 1)

    rec(root, 0)
    if args.json:
        print(json.dumps(found, ensure_ascii=False, indent=2))
    else:
        for it in found:
            mark = "/" if it["is_dir"] else ""
            print(f"{fmt_size(it['size']):>10}  {it['path']}{mark}")
    return 0


def fnmatch_to_re(pattern: str) -> str:
    out = []
    for ch in pattern:
        if ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def cmd_tree(client: MinasWebDAV, args: argparse.Namespace) -> int:
    root = ensure_data_path(args.path)

    def rec(path: str, prefix: str, depth: int) -> None:
        try:
            items = client.propfind(path, depth="1", with_mtls=True)
        except Exception:
            return
        items = [it for it in items if norm(it["path"]) != norm(path)]
        items.sort(key=lambda x: (not x["is_dir"], x["name"]))
        for i, it in enumerate(items):
            last = i == len(items) - 1
            branch = "└── " if last else "├── "
            extra = "/" if it["is_dir"] else f"  ({fmt_size(it['size'])})"
            print(f"{prefix}{branch}{it['name']}{extra}")
            if it["is_dir"] and depth < args.max_depth:
                rec(it["path"], prefix + ("    " if last else "│   "), depth + 1)

    print(root)
    rec(root, "", 0)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="minas-webdav", description="Xiaomi Smart Storage WebDAV CLI for agents")
    p.add_argument("--ip", default=DEFAULT_NAS_IP, help="NAS IP (default env MINAS_IP or 192.168.1.100)")
    p.add_argument("--port", type=int, default=DEFAULT_WEBDAV_PORT, help="WebDAV port (default 5000)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("ls", help="list directory")
    sp.add_argument("path", nargs="?", default=DATA_ROOT)
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--self-only", action="store_true", help="only stat the path itself")
    sp.add_argument("--long", action="store_true", default=True)
    sp.set_defaults(func=cmd_ls)

    sp = sub.add_parser("stat", help="stat one path")
    sp.add_argument("path")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_stat)

    sp = sub.add_parser("du", help="directory sizes")
    sp.add_argument("path", nargs="?", default=DATA_ROOT)
    sp.add_argument("--max-depth", type=int, default=2)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_du)

    sp = sub.add_parser("df", help="pool capacity")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_df)

    sp = sub.add_parser("get", help="download file")
    sp.add_argument("remote")
    sp.add_argument("local")
    sp.set_defaults(func=cmd_get)

    sp = sub.add_parser("put", help="upload file")
    sp.add_argument("local")
    sp.add_argument("remote")
    sp.set_defaults(func=cmd_put)

    sp = sub.add_parser("cat", help="print file to stdout")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_cat)

    sp = sub.add_parser("mkdir", help="create directory (with parents)")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_mkdir)

    sp = sub.add_parser("rm", help="delete file/dir")
    sp.add_argument("path")
    sp.add_argument("-r", "--recursive", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_rm)

    sp = sub.add_parser("mv", help="move/rename")
    sp.add_argument("src")
    sp.add_argument("dst")
    sp.add_argument("-f", "--force", action="store_true")
    sp.set_defaults(func=cmd_mv)

    sp = sub.add_parser("cp", help="copy")
    sp.add_argument("src")
    sp.add_argument("dst")
    sp.add_argument("-f", "--force", action="store_true")
    sp.set_defaults(func=cmd_cp)

    sp = sub.add_parser("find", help="find by name pattern")
    sp.add_argument("path", nargs="?", default=DATA_ROOT)
    sp.add_argument("--name", help="wildcard, e.g. '*.mp4'")
    sp.add_argument("--max-depth", type=int, default=3)
    sp.add_argument("--files", action="store_true")
    sp.add_argument("--dirs", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_find)

    sp = sub.add_parser("tree", help="print tree")
    sp.add_argument("path", nargs="?", default=DATA_ROOT)
    sp.add_argument("--max-depth", type=int, default=2)
    sp.set_defaults(func=cmd_tree)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    client = MinasWebDAV(ip=args.ip, port=args.port)
    try:
        return args.func(client, args)
    except KeyboardInterrupt:
        return 130
    except RuntimeError as e:
        msg = str(e)
        print(msg, file=sys.stderr)
        if "cert" in msg.lower() or "discover" in msg.lower():
            return 3
        return 1
    except Exception as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
