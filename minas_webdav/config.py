"""Central config loader for minas-webdav.

Non-secret settings (host/port/cn/certs): ``minas_config.json`` next to this
file (gitignored; see ``minas_config.example.json``). Override with
``MINAS_CONFIG``. Secrets: ``credentials.env`` OUTSIDE any repo, default
``%LOCALAPPDATA%\\minasCred\\credentials.env`` — two lines::

    MINAS_USER=...
    MINAS_PASS=...

Environment variables already set always win (setdefault semantics).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(
    os.environ.get("MINAS_CONFIG") or PACKAGE_DIR / "minas_config.json"
)
CRED_PATH = Path(
    os.environ.get("MINAS_CRED_FILE")
    or Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "minasCred" / "credentials.env"
)

_DEFAULTS = {
    "host": "192.168.1.100",
    "port": 5000,
    "cn": "",
    "data_root": "/pool0/data",
    "cert_home": "%LOCALAPPDATA%\\minasCert",
    "proxy": {"listen_host": "127.0.0.1", "listen_port": 8899},
}


def _expand(value: str) -> str:
    return os.path.expandvars(os.path.expanduser(value))


def load_config() -> dict:
    cfg = dict(_DEFAULTS)
    if CONFIG_PATH.is_file():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def load_credentials() -> tuple[str, str]:
    user = os.environ.get("MINAS_USER", "")
    password = os.environ.get("MINAS_PASS", "")
    if user and password:
        return user, password
    if CRED_PATH.is_file():
        for line in CRED_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if key == "MINAS_USER" and not user:
                user = val
            elif key == "MINAS_PASS" and not password:
                password = val
    return user, password


def apply_env() -> dict:
    """Push config + credentials into os.environ (setdefault) and return config."""
    cfg = load_config()
    user, password = load_credentials()
    os.environ.setdefault("MINAS_IP", str(cfg["host"]))
    os.environ.setdefault("MINAS_PORT", str(cfg["port"]))
    os.environ.setdefault("MINAS_CN", str(cfg.get("cn") or ""))
    os.environ.setdefault("MINAS_CERT_HOME", _expand(str(cfg.get("cert_home", ""))))
    os.environ.setdefault("MINAS_DATA_ROOT", str(cfg.get("data_root", "/pool0/data")))
    if user:
        os.environ.setdefault("MINAS_USER", user)
    if password:
        os.environ.setdefault("MINAS_PASS", password)
    return cfg
