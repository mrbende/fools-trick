"""Minimal synchronous Redis client over a raw TCP socket.

RESP is simple enough that a dependency is not worth it: we send RESP arrays and parse
RESP replies. Used for the hot short-term tier + the write-stream. If Redis is down,
callers treat it as a soft failure (memory still works via SQLite) -- this client raises
RedisUnavailable on connect/command failure, which callers swallow.

Synchronous by design: the core is invoked per tool-call from a subprocess, so a blocking
socket is the right model. One connection, commands issued in order (Redis is
single-threaded and replies in order).
"""

from __future__ import annotations

import socket
from typing import Optional
from urllib.parse import urlparse


class RedisUnavailable(Exception):
    """Raised when Redis cannot be reached or a command fails. Callers swallow it."""


def _encode(args: tuple) -> bytes:
    out = [f"*{len(args)}\r\n".encode()]
    for a in args:
        v = str(a).encode()
        out.append(f"${len(v)}\r\n".encode())
        out.append(v)
        out.append(b"\r\n")
    return b"".join(out)


class Redis:
    """One persistent connection. Lazily connects on first command."""

    def __init__(self, url: str = "redis://127.0.0.1:6379", timeout: float = 1.5):
        u = urlparse(url)
        self.host = u.hostname or "127.0.0.1"
        self.port = u.port or 6379
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._buf = b""

    def _connect(self) -> None:
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            self._sock = None
            raise RedisUnavailable(str(e)) from e

    def cmd(self, *args):
        """Send one command and return its decoded reply. Raises RedisUnavailable on failure."""
        if self._sock is None:
            self._connect()
        try:
            self._sock.sendall(_encode(args))
            return self._read_reply()
        except (OSError, RedisUnavailable) as e:
            self.close()
            raise RedisUnavailable(str(e)) from e

    def _read_reply(self):
        while True:
            parsed = self._parse(self._buf, 0)
            if parsed is not None:
                value, nxt = parsed
                self._buf = self._buf[nxt:]
                if isinstance(value, _RespError):
                    raise RedisUnavailable(value.msg)
                return value
            chunk = self._sock.recv(65536)
            if not chunk:
                raise RedisUnavailable("connection closed")
            self._buf += chunk

    def _parse(self, buf: bytes, pos: int):
        if pos >= len(buf):
            return None
        t = buf[pos : pos + 1]
        nl = buf.find(b"\r\n", pos)
        if nl == -1:
            return None
        line = buf[pos + 1 : nl].decode()
        after = nl + 2
        if t == b"+":
            return (line, after)
        if t == b"-":
            return (_RespError(line), after)
        if t == b":":
            return (int(line), after)
        if t == b"$":
            n = int(line)
            if n == -1:
                return (None, after)
            if after + n + 2 > len(buf):
                return None
            return (buf[after : after + n].decode(), after + n + 2)
        if t == b"*":
            n = int(line)
            if n == -1:
                return (None, after)
            arr = []
            p = after
            for _ in range(n):
                r = self._parse(buf, p)
                if r is None:
                    return None
                arr.append(r[0])
                p = r[1]
            return (arr, p)
        return (line, after)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._buf = b""


class _RespError:
    def __init__(self, msg: str):
        self.msg = msg
