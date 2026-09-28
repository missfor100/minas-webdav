"""MCP server: expose minas-webdav as structured tools over stdio.

Usage:
  python -m minas_webdav.mcp_server
  (or .venv\\Scripts\\python.exe with PYTHONPATH=F:\\Projects\\minas-webdav)

Shared core with the CLI (minas_webdav.cli / minas_webdav.config).
"""
from __future__ import annotations

import io
import json
import re
import contextlib
from typing import Any

from mcp.server.mcpserver import MCPServer  # mcp 2.x (FastMCP renamed)
from mcp.types import ToolAnnotations

from . import config as _mconf

_mconf.apply_env()

from .cli import (  # noqa: E402
    DATA_ROOT,
    MinasWebDAV,
    dest_header,
    ensure_data_path,
    fnmatch_to_re,
    norm,
)

mcp = MCPServer("minas-webdav")

MAX_PUT_BYTES = 32 * 1024 * 1024
MAX_CAT_BYTES = 64 * 1024


def _client() -> MinasWebDAV:
    return MinasWebDAV()


def _safe_path(path: str) -> str:
    p = ensure_data_path(path)
    if ".." in p.split("/"):
        raise ValueError("path must stay under /pool0/data (no '..')")
    return p


def _hint(err: Exception) -> str:
    msg = str(err)
    low = msg.lower()
    if "cert" in low or "discover" in low or "credential" in low or "451" in msg:
        return "check %LOCALAPPDATA%\\minasCred\\credentials.env and minasCert (official client login)"
    if "423" in msg:
        return "resource is locked (locks linger up to 60s after delete); retry later or use another path"
    if "403" in msg:
        return "PROPFIND Depth: infinity is forbidden on this device; use max_depth and walk level by level"
    return "see SKILL minas-webdav / docs/webdav-surface.md"


def _list_dir(client: MinasWebDAV, path: str) -> list[dict[str, Any]]:
    items = client.propfind(path, depth="1", with_mtls=True)
    return [it for it in items if norm(it["path"]) != norm(path)]


def _ok(**data: Any) -> dict[str, Any]:
    return {"ok": True, **data}


def _fail(err: Exception) -> dict[str, Any]:
    return {"ok": False, "error": f"{type(err).__name__}: {err}", "hint": _hint(err)}


READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)
NONIDEM = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)


@mcp.tool(annotations=READ)
def nas_ls(path: str = DATA_ROOT, limit: int = 500) -> dict[str, Any]:
    """List one directory under /pool0/data (default root). Returns up to `limit` entries, sorted dirs-first."""
    p = _safe_path(path)
    try:
        items = sorted(_list_dir(_client(), p), key=lambda x: (not x["is_dir"], x["name"]))
    except Exception as e:
        return _fail(e)
    truncated = len(items) > limit
    return _ok(path=p, count=len(items), truncated=truncated, entries=items[:limit])


@mcp.tool(annotations=READ)
def nas_stat(path: str) -> dict[str, Any]:
    """Metadata for a single path (exists check, size, modified, is_dir)."""
    p = _safe_path(path)
    try:
        items = _client().propfind(p, depth="0", with_mtls=True)
    except Exception as e:
        return _fail(e)
    if not items:
        return {"ok": False, "error": "not found", "path": p}
    return _ok(**items[0])


@mcp.tool(annotations=READ)
def nas_tree(path: str = DATA_ROOT, max_depth: int = 2) -> dict[str, Any]:
    """Tree listing with bounded depth (max_depth <= 4 enforced). Use instead of recursive ls."""
    p = _safe_path(path)
    max_depth = max(0, min(int(max_depth), 4))
    nodes: list[dict[str, Any]] = []
    try:
        def rec(cur: str, depth: int) -> None:
            for it in _list_dir(_client(), cur):
                nodes.append({"depth": depth, **it})
                if it["is_dir"] and depth < max_depth:
                    rec(it["path"], depth + 1)

        rec(p, 0)
    except Exception as e:
        return _fail(e)
    return _ok(path=p, max_depth=max_depth, count=len(nodes), nodes=nodes)


@mcp.tool(annotations=READ)
def nas_find(
    path: str = DATA_ROOT,
    name: str | None = None,
    max_depth: int = 3,
    files_only: bool = False,
    dirs_only: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    """Find by name wildcard (e.g. '*.mp4') under `path`, bounded depth (max 4). Returns matches with sizes."""
    p = _safe_path(path)
    max_depth = max(0, min(int(max_depth), 4))
    name_re = re.compile(fnmatch_to_re(name)) if name else None
    found: list[dict[str, Any]] = []
    try:
        def rec(cur: str, depth: int) -> None:
            if len(found) >= limit:
                return
            for it in _list_dir(_client(), cur):
                ok = True
                if name_re and not name_re.search(it["name"]):
                    ok = False
                if files_only and it["is_dir"]:
                    ok = False
                if dirs_only and not it["is_dir"]:
                    ok = False
                if ok:
                    found.append(it)
                if it["is_dir"] and depth < max_depth:
                    rec(it["path"], depth + 1)

        rec(p, 0)
    except Exception as e:
        return _fail(e)
    return _ok(path=p, count=len(found), truncated=len(found) >= limit, matches=found[:limit])


@mcp.tool(annotations=READ)
def nas_du(path: str = DATA_ROOT, max_depth: int = 2) -> dict[str, Any]:
    """Per-subdirectory size rollup under `path` (bytes + file count), bounded depth (max 3)."""
    p = _safe_path(path)
    max_depth = max(0, min(int(max_depth), 3))
    client = _client()

    def walk(cur: str, depth: int) -> tuple[int, int]:
        total, count = 0, 0
        try:
            items = _list_dir(client, cur)
        except Exception:
            return 0, 0
        for it in items:
            if it["is_dir"]:
                if depth < max_depth:
                    st, sc = walk(it["path"], depth + 1)
                    total += st
                    count += sc
            else:
                total += it["size"]
                count += 1
        return total, count

    rows = []
    try:
        for it in _list_dir(client, p):
            if it["is_dir"]:
                total, count = walk(it["path"], 0)
                rows.append({"name": it["name"], "is_dir": True, "bytes": total, "files": count})
            else:
                rows.append({"name": it["name"], "is_dir": False, "bytes": it["size"], "files": 1})
    except Exception as e:
        return _fail(e)
    rows.sort(key=lambda r: -r["bytes"])
    return _ok(path=p, max_depth=max_depth, total_bytes=sum(r["bytes"] for r in rows), rows=rows)


@mcp.tool(annotations=READ)
def nas_df() -> dict[str, Any]:
    """Storage pool capacity: total / used / free (bytes and human-readable)."""
    try:
        payload = _client().discover()
        pool = (payload.get("data") or {}).get("internal_pool") or []
        if not pool:
            return {"ok": False, "error": "pool info unavailable", "hint": _hint(RuntimeError("discover"))}
        p0 = pool[0]
        total = int(p0.get("total_size") or 0)
        used = int(p0.get("used_size") or 0)
        return _ok(
            name=p0.get("name"),
            data_dir=p0.get("data_dir"),
            total_bytes=total,
            used_bytes=used,
            free_bytes=total - used,
        )
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=READ)
def nas_cat(path: str, max_bytes: int = MAX_CAT_BYTES) -> dict[str, Any]:
    """Print a text file (truncated to max_bytes, default 64KB). For large files use nas_get."""
    p = _safe_path(path)
    max_bytes = max(1, min(int(max_bytes), MAX_CAT_BYTES))
    try:
        status, _, data = _client().request("GET", p, with_mtls=True)
    except Exception as e:
        return _fail(e)
    if status == 404:
        return {"ok": False, "error": f"not found: {p}"}
    if status not in (200, 206):
        return _fail(RuntimeError(f"GET {p} -> HTTP {status}"))
    truncated = len(data) > max_bytes
    return _ok(path=p, bytes=len(data), truncated=truncated, text=data[:max_bytes].decode("utf-8", "replace"))


@mcp.tool(annotations=WRITE)
def nas_get(remote: str, local: str) -> dict[str, Any]:
    """Download one file to a local path. Large files: prefer the CLI. Single call max 32MB body check is best-effort."""
    from pathlib import Path

    p = _safe_path(remote)
    try:
        status, _, data = _client().request("GET", p, with_mtls=True)
        if status == 404:
            return {"ok": False, "error": f"not found: {p}"}
        if status not in (200, 206):
            return _fail(RuntimeError(f"GET {p} -> HTTP {status}"))
        dest = Path(local)
        if dest.is_dir():
            dest = dest / norm(p).rstrip("/").split("/")[-1]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    except Exception as e:
        return _fail(e)
    return _ok(path=p, local=str(dest), bytes=len(data))


@mcp.tool(annotations=WRITE)
def nas_put(local: str, remote: str) -> dict[str, Any]:
    """Upload one local file to /pool0/data/... (creates/overwrites). Max 32MB per call; larger via CLI."""
    from pathlib import Path

    src = Path(local)
    if not src.exists() or src.is_dir():
        return {"ok": False, "error": f"local file not found: {local}"}
    size = src.stat().st_size
    if size > MAX_PUT_BYTES:
        return {"ok": False, "error": f"file too large ({size} bytes > {MAX_PUT_BYTES})", "hint": "use the CLI for large files"}
    p = _safe_path(remote)
    try:
        status, _, body = _client().request(
            "PUT", p, body=src.read_bytes(),
            headers={"Content-Type": "application/octet-stream"}, with_mtls=True,
        )
    except Exception as e:
        return _fail(e)
    if status not in (200, 201, 204):
        return _fail(RuntimeError(f"PUT {p} -> HTTP {status}: {body[:160]!r}"))
    return _ok(path=p, local=str(src), bytes=size, status=status)


@mcp.tool(annotations=WRITE)
def nas_mkdir(path: str) -> dict[str, Any]:
    """Create a directory (including parents) under /pool0/data."""
    p = _safe_path(path)
    client = _client()
    cur = ""
    try:
        for part in norm(p).strip("/").split("/"):
            cur += "/" + part
            status, _, body = client.request("MKCOL", cur + "/", with_mtls=True)
            if status not in (201, 204, 301, 405):
                return _fail(RuntimeError(f"MKCOL {cur} -> HTTP {status}: {body[:160]!r}"))
    except Exception as e:
        return _fail(e)
    return _ok(path=p)


@mcp.tool(annotations=NONIDEM)
def nas_mv(src: str, dst: str, force: bool = False) -> dict[str, Any]:
    """Move/rename within /pool0/data (Destination is percent-encoded by the server wrapper). Not idempotent."""
    s, d = _safe_path(src), _safe_path(dst)
    try:
        status, _, body = _client().request(
            "MOVE", s,
            headers={"Destination": dest_header(d, _client()), "Overwrite": "T" if force else "F"},
            with_mtls=True,
        )
    except Exception as e:
        return _fail(e)
    if status not in (200, 201, 204):
        return _fail(RuntimeError(f"MOVE {s} -> HTTP {status}: {body[:160]!r}"))
    return _ok(src=s, dst=d)


@mcp.tool(annotations=WRITE)
def nas_cp(src: str, dst: str, force: bool = False) -> dict[str, Any]:
    """Copy within /pool0/data. Idempotent when force=True (overwrites dst)."""
    s, d = _safe_path(src), _safe_path(dst)
    try:
        status, _, body = _client().request(
            "COPY", s,
            headers={"Destination": dest_header(d, _client()), "Overwrite": "T" if force else "F"},
            with_mtls=True,
        )
    except Exception as e:
        return _fail(e)
    if status not in (200, 201, 204):
        return _fail(RuntimeError(f"COPY {s} -> HTTP {status}: {body[:160]!r}"))
    return _ok(src=s, dst=d)


@mcp.tool(annotations=DESTRUCTIVE)
def nas_rm(path: str, recursive: bool = False, confirm: bool = False) -> dict[str, Any]:
    """Delete a file or directory. DESTRUCTIVE. recursive=True requires confirm=True — only after the user explicitly agreed."""
    if recursive and not confirm:
        raise ValueError(
            "recursive delete requires confirm=true. Ask the user for explicit consent first."
        )
    p = _safe_path(path)
    client = _client()

    def rm(cur: str) -> None:
        for it in _list_dir(client, cur):
            if it["is_dir"]:
                rm(it["path"])
            else:
                client.request("DELETE", it["path"], with_mtls=True)

    try:
        if recursive:
            rm(p)
        status, _, body = client.request("DELETE", p, with_mtls=True)
        if status == 423:
            return _fail(RuntimeError(f"DELETE {p} -> 423 locked"))
        if status not in (200, 204, 404):
            return _fail(RuntimeError(f"DELETE {p} -> HTTP {status}: {body[:160]!r}"))
    except Exception as e:
        return _fail(e)
    return _ok(path=p, recursive=recursive, status=status)


@mcp.tool(annotations=READ)
def nas_probe() -> dict[str, Any]:
    """Run the WebDAV capability probe (creates unique temp paths under /pool0/data and cleans up). Self-check after setup."""
    from . import probe as probe_mod

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = probe_mod.run([])
    except Exception as e:
        return _fail(e)
    out = buf.getvalue()
    return _ok(exit_code=code, summary=out[-2000:], full_log_bytes=len(out))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
