#!/usr/bin/env python3
"""Request guard for the local web servers (stdlib only).

* Writes are POST only and need the per-start token in the ``X-Usage-Token`` header.
  The server writes a fresh random token to a user-only file (mode 600) at start-up and
  embeds it in the page it serves, so the page's own buttons keep working; command-line
  helpers read the file.
* ``Host`` must be 127.0.0.1 / localhost / [::1] (optionally with the server's port):
  a DNS-rebinding page whose Host is ``evil.example`` is refused before anything runs.
* Writes with a cross-site ``Origin`` / ``Referer`` / ``Sec-Fetch-Site`` are refused.
"""
import hmac
import os
import re
import secrets
from urllib.parse import urlsplit

TOKEN_HEADER = "X-Usage-Token"
LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")


def content_length(headers):
    """The request's Content-Length as an int >= 0 (a missing or empty header is 0). Raises ValueError for anything
    else (negative, ``abc``, ``1e3``): ``rfile.read(-1)`` would wait for the client to close the connection."""
    v = (headers.get("Content-Length") or "").strip()
    if not v:
        return 0
    if not re.fullmatch(r"[0-9]+", v):
        raise ValueError("bad Content-Length")
    return int(v)


def new_token():
    return secrets.token_urlsafe(32)


def write_token_file(path, token):
    """Write ``token`` to ``path`` readable by the current user only (chmod 600)."""
    path = os.path.abspath(str(path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (token + "\n").encode("ascii"))
    finally:
        os.close(fd)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def read_token_file(path):
    try:
        with open(path, encoding="ascii") as f:
            return f.read().strip() or None
    except OSError:
        return None


def remove_token_file(path, token):
    """Delete the file only if it still holds our token (another server may have replaced it)."""
    try:
        if read_token_file(path) == token:
            os.remove(path)
    except OSError:
        pass


def split_host(value):
    """'localhost:8765' -> ('localhost', 8765); '[::1]:80' -> ('::1', 80)."""
    v = (value or "").strip().lower()
    if not v:
        return "", None
    if v.startswith("["):
        end = v.find("]")
        if end < 0:
            return v, None
        host, rest = v[1:end], v[end + 1:]
        port = rest[1:] if rest.startswith(":") else ""
    elif v.count(":") == 1:
        host, port = v.split(":", 1)
    else:
        host, port = v, ""
    if port:
        if not port.isdigit():
            return host, -1
        return host, int(port)
    return host, None


def host_allowed(host_header, port, extra_hosts=()):
    """True when the Host header names this machine's loopback (or an explicitly allowed name)."""
    host, p = split_host(host_header)
    allowed = set(LOOPBACK_NAMES) | {str(h).strip().lower().strip("[]") for h in extra_hosts if str(h).strip()}
    if host not in allowed:
        return False
    return p is None or p == int(port)


def _origin_ok(url, port, extra_hosts):
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    if u.scheme not in ("http", "https"):
        return False
    return host_allowed(u.netloc, port, extra_hosts)


def same_origin_write(headers, port, extra_hosts=()):
    """Reject a write that a browser marks as coming from another site.

    Requests without Origin/Referer (curl, scripts) pass this check; they still need the token."""
    sfs = (headers.get("Sec-Fetch-Site") or "").strip().lower()
    if sfs in ("cross-site", "same-site"):
        return False, f"cross-site request (Sec-Fetch-Site: {sfs})"
    origin = headers.get("Origin")
    if origin is not None:
        if origin.strip().lower() == "null" or not _origin_ok(origin.strip(), port, extra_hosts):
            return False, "foreign Origin"
    referer = headers.get("Referer")
    if referer and not _origin_ok(referer.strip(), port, extra_hosts):
        return False, "foreign Referer"
    return True, ""


def token_ok(headers, token):
    got = (headers.get(TOKEN_HEADER) or "").strip()
    return bool(token) and bool(got) and hmac.compare_digest(got.encode(), token.encode())


def check_write(headers, port, token, extra_hosts=()):
    """(ok, http_status, reason) for a state-changing request."""
    ok, why = same_origin_write(headers, port, extra_hosts)
    if not ok:
        return False, 403, why
    if not token_ok(headers, token):
        return False, 403, f"missing or wrong {TOKEN_HEADER} header"
    return True, 200, ""


def token_meta(token):
    """<meta> tag to embed in a served page (the token is URL-safe base64, no escaping needed)."""
    return f'<meta name="usage-token" content="{token}">'


def inject_token(html_text, token):
    meta = token_meta(token)
    low = html_text[:4000].lower()
    i = low.find("<head>")
    if i >= 0:
        return html_text[:i + 6] + meta + html_text[i + 6:]
    return meta + html_text
