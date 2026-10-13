"""Egress allowlist proxy for the review sandbox: the reviewers' only way out.

Reviewer containers sit on an internal docker network with no route outside; this proxy is
attached to that network and to the default one. It answers HTTP CONNECT for allowlisted
host:port pairs only (default api.anthropic.com:443) and refuses everything else: plain HTTP,
other hosts, IP literals, other ports. TLS stays end to end (the proxy never sees content).
Optional EGRESS_UPSTREAM=http://host:port chains to a corporate/egress proxy.

Standard library only; runs as nobody in a read-only container. Logs one JSON line per decision.
"""
import ipaddress
import json
import os
import select
import socket
import socketserver
import sys
import threading
import time

ALLOW = {a.strip().lower() for a in os.environ.get("EGRESS_ALLOW", "api.anthropic.com:443").split(",") if a.strip()}
UPSTREAM = os.environ.get("EGRESS_UPSTREAM", "").strip()
PORT = int(os.environ.get("EGRESS_PORT", "3128"))
MAX_CONNECTIONS = int(os.environ.get("EGRESS_MAX_CONNECTIONS", "64"))
HEAD_TIMEOUT = 10
IDLE_TIMEOUT = 300
MAX_HEAD = 8192

_slots = threading.BoundedSemaphore(MAX_CONNECTIONS)


def log(**kw):
    print(json.dumps({"ts": int(time.time()), **kw}), file=sys.stderr, flush=True)


def allowed(target: str) -> bool:
    host, _, port = target.rpartition(":")
    if not host or not port.isdigit():
        return False
    try:
        ipaddress.ip_address(host.strip("[]"))
        return False  # IP literals never: the allowlist is by name
    except ValueError:
        pass
    return target.lower() in ALLOW


def read_head(sock) -> bytes:
    sock.settimeout(HEAD_TIMEOUT)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(1024)
        if not chunk:
            break
        data += chunk
        if len(data) > MAX_HEAD:
            break
    return data


def open_upstream(target: str):
    if not UPSTREAM:
        host, _, port = target.rpartition(":")
        return socket.create_connection((host, int(port)), timeout=15)
    up = UPSTREAM.split("://", 1)[-1].rstrip("/")
    host, _, port = up.rpartition(":")
    sock = socket.create_connection((host, int(port)), timeout=15)
    sock.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    head = read_head(sock)
    if head.split(b"\r\n", 1)[0].split(b" ")[1:2] != [b"200"]:
        sock.close()
        raise OSError("upstream refused: " + head.split(b"\r\n", 1)[0].decode("latin-1")[:80])
    return sock


def pump(a, b):
    a.settimeout(None)
    b.settimeout(None)
    sockets = [a, b]
    while True:
        ready, _, _ = select.select(sockets, [], [], IDLE_TIMEOUT)
        if not ready:
            return
        for s in ready:
            data = s.recv(65536)
            if not data:
                return
            (b if s is a else a).sendall(data)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        if not _slots.acquire(blocking=False):
            self.request.sendall(b"HTTP/1.1 503 Busy\r\n\r\n")
            return
        try:
            self._handle()
        except OSError as exc:
            log(event="egress_error", error=str(exc)[:120])
        finally:
            _slots.release()

    def _handle(self):
        head = read_head(self.request)
        line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = line.split(" ")
        target = parts[1] if len(parts) == 3 else ""
        if len(parts) != 3 or parts[0] != "CONNECT" or not parts[2].startswith("HTTP/1.") or not allowed(target):
            log(event="egress", allow=False, request=line[:120])
            self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return
        upstream = open_upstream(target)
        log(event="egress", allow=True, target=target)
        with upstream:
            self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            pump(self.request, upstream)


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    log(event="egress_start", allow=sorted(ALLOW), upstream=bool(UPSTREAM), port=PORT)
    with Server(("0.0.0.0", PORT), Handler) as srv:
        srv.serve_forever()
