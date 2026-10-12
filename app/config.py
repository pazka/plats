"""Runtime configuration, read from environment variables (prefix PLATS_)."""
from __future__ import annotations

import ipaddress
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TRUSTED_PROXIES = "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default).strip()
    # Tolerate values quoted in an env file that was passed verbatim (docker --env-file).
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def _bool(name: str, default: bool) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    return raw in {"1", "true", "yes", "on"}


def _load_or_create_secret(data_dir: Path, name: str) -> str:
    """Secrets not given by env are generated once and persisted in the data volume."""
    path = data_dir / ".secrets" / name
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    value = secrets.token_urlsafe(48)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(value)
    return value


@dataclass
class Settings:
    data_dir: Path
    secret_key: str
    ip_salt: str
    admin_password_hash: str
    admin_totp_secret: str
    cookie_secure: bool
    trusted_proxies: list = field(default_factory=list)
    base_url: str = "https://plats.hosh.it"
    contact: str = ""
    log_client_ip: bool = False
    pow_difficulty: int = 16

    @property
    def db_path(self) -> Path:
        return self.data_dir / "plats.sqlite3"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def admin_configured(self) -> bool:
        return bool(self.admin_password_hash and self.admin_totp_secret)

    def is_trusted_proxy(self, host: str | None) -> bool:
        if not host:
            return False
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            return False
        return any(addr in net for net in self.trusted_proxies)


def load_settings() -> Settings:
    data_dir = Path(_env("PLATS_DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    secret_key = _env("PLATS_SECRET_KEY") or _load_or_create_secret(data_dir, "secret_key")
    ip_salt = _env("PLATS_IP_SALT") or _load_or_create_secret(data_dir, "ip_salt")
    proxies = []
    for part in _env("PLATS_TRUSTED_PROXIES", DEFAULT_TRUSTED_PROXIES).split(","):
        part = part.strip()
        if part:
            proxies.append(ipaddress.ip_network(part, strict=False))
    return Settings(
        data_dir=data_dir,
        secret_key=secret_key,
        ip_salt=ip_salt,
        admin_password_hash=_env("PLATS_ADMIN_PASSWORD_HASH"),
        admin_totp_secret=_env("PLATS_ADMIN_TOTP_SECRET"),
        cookie_secure=_bool("PLATS_COOKIE_SECURE", True),
        trusted_proxies=proxies,
        base_url=_env("PLATS_BASE_URL", "https://plats.hosh.it").rstrip("/"),
        contact=_env("PLATS_CONTACT", ""),
        log_client_ip=_bool("PLATS_LOG_CLIENT_IP", False),
        pow_difficulty=int(_env("PLATS_POW_DIFFICULTY", "16") or 16),
    )
