"""Persistent RAM-only JSON bridge to the existing scoped Go TLS/H2 helper.

The Go process owns its connection pools and Cookie jar. This module does not
navigate, bootstrap, execute JavaScript, inspect browser state, or write evidence.
The Cookie view covers the last requested URL; it is not a separately queried
dump of the Go jar. System proxy discovery is read-only and occurs once only
when the caller explicitly selects network_route='system'.
"""
from __future__ import annotations

import json as json_module
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import unquote, urlsplit

_HERE = Path(__file__).resolve().parent
_UNSET = object()
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_MAX_STDOUT_BYTES = 32 * 1024 * 1024
_KNOWN_ERRORS = frozenset(("ScopeError", "ClientInitError", "ProxyChangeError", "RequestInitError",
                           "TransportError", "ResponseReadError"))
_SKIP_HEADERS = frozenset(("host", "content-length", "connection", "authorization", "proxy-authorization"))


def _build_backend(executable):
    """Build from pinned Go sources on first use or after a source update."""
    directory = executable.parent
    sources = [directory / "go.mod", directory / "go.sum", *directory.glob("*.go")]
    if not all(path.is_file() for path in sources):
        raise NativeTransportError("Native Go sources are incomplete")
    if executable.is_file() and executable.stat().st_mtime_ns >= max(path.stat().st_mtime_ns for path in sources):
        return
    go = shutil.which("go")
    if not go:
        raise NativeTransportError("Install Go 1.26+; the HTTP backend is compiled automatically on first run")
    temporary = directory / f".{executable.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        result = subprocess.run([go, "build", "-trimpath", "-o", str(temporary), "."],
                                cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=180, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode:
            raise NativeTransportError("Go backend build failed; run go build in _common/native_http to inspect compiler diagnostics")
        try:
            temporary.replace(executable)
        except OSError:
            if not executable.is_file():
                raise
    except subprocess.TimeoutExpired:
        raise NativeTimeoutError("Go backend build timed out") from None
    except OSError:
        raise NativeTransportError("Go backend build could not complete") from None
    finally:
        temporary.unlink(missing_ok=True)


class NativeTransportError(RuntimeError):
    """Sanitized transport error; never includes a response, header or URL."""


class NativeTimeoutError(NativeTransportError):
    pass


class NativeProtocolError(NativeTransportError):
    pass


class NativeHTTPError(NativeTransportError):
    pass


class NativeCookie:
    __slots__ = ("name", "value", "domain", "path", "secure", "http_only")

    def __init__(self, record):
        self.name = str(record["Name"])
        self.value = str(record.get("Value", ""))
        self.domain = str(record.get("Domain", ""))
        self.path = str(record.get("Path", ""))
        self.secure = bool(record.get("Secure", False))
        self.http_only = bool(record.get("HttpOnly", False))

    def __repr__(self):
        return f"<NativeCookie name={self.name!r} value=[RAM only]>"


class NativeCookieView:
    """Iterable requests-style Cookie objects applicable to the latest URL."""

    def __init__(self, records=()):
        self._cookies = tuple(NativeCookie(item) for item in records)

    def __iter__(self):
        return iter(self._cookies)

    def __len__(self):
        return len(self._cookies)

    def get(self, name, default=None, *, domain=None, path=None):
        matches = [cookie for cookie in self._cookies if cookie.name == name
                   and (domain is None or cookie.domain == domain)
                   and (path is None or cookie.path == path)]
        if len(matches) > 1:
            raise NativeProtocolError("Cookie name is ambiguous in the latest URL view")
        return matches[0].value if matches else default

    def __getitem__(self, name):
        value = self.get(name, _UNSET)
        if value is _UNSET:
            raise KeyError(name)
        return value

    def names(self):
        return sorted({cookie.name for cookie in self._cookies})

    def __repr__(self):
        return f"<NativeCookieView names={self.names()!r} values=[RAM only]>"


class NativeHeaders(Mapping):
    def __init__(self, values):
        if not isinstance(values, dict):
            raise NativeProtocolError("Invalid native response header shape")
        self._values = {}
        for name, entries in values.items():
            if not isinstance(name, str) or not isinstance(entries, list) or not all(isinstance(item, str) for item in entries):
                raise NativeProtocolError("Invalid native response header shape")
            self._values[name.lower()] = (name, tuple(entries))

    def __getitem__(self, key):
        return ", ".join(self._values[key.lower()][1])

    def __iter__(self):
        return (name for name, _ in self._values.values())

    def __len__(self):
        return len(self._values)

    def get_all(self, key):
        entry = self._values.get(key.lower())
        return entry[1] if entry else ()

    def __repr__(self):
        return f"<NativeHeaders names={list(self)!r} values=[RAM only]>"


class NativeResponse:
    """Buffered UTF-8 text response; Go's protocol is not a binary-body channel."""

    def __init__(self, envelope, *, url, method):
        status = envelope.get("status")
        text = envelope.get("body", "")
        cookies = envelope.get("cookies") or []
        if type(status) is not int or not 100 <= status <= 599 or not isinstance(text, str):
            raise NativeProtocolError("Invalid native response shape")
        if not isinstance(cookies, list) or any(not isinstance(item, dict) or not isinstance(item.get("Name"), str) for item in cookies):
            raise NativeProtocolError("Invalid native Cookie shape")
        self.status_code = status
        self.text = text
        self.headers = NativeHeaders(envelope.get("headers") or {})
        self.cookies = NativeCookieView(cookies)
        self.http_version = envelope.get("http_version")
        self.alpn = envelope.get("alpn")
        self.tls_version = envelope.get("tls_version")
        self.url = url
        self.request_method = method

    @property
    def content(self):
        return self.text.encode("utf-8")

    @property
    def ok(self):
        return self.status_code < 400

    def json(self, **kwargs):
        return json_module.loads(self.text, **kwargs)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise NativeHTTPError(f"HTTP {self.status_code}")

    def close(self):
        pass  # Go fully consumed and closed the response before emitting JSON.

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __repr__(self):
        return f"<NativeResponse [{self.status_code}]>"


def _read_stdout(stream, messages):
    try:
        while True:
            line = stream.readline(_MAX_STDOUT_BYTES + 1)
            if not line:
                messages.put(("closed", None))
                return
            if len(line) > _MAX_STDOUT_BYTES or not line.endswith(b"\n"):
                messages.put(("invalid", None))
                return
            messages.put(("line", line))
    except (OSError, ValueError):
        messages.put(("closed", None))


def _discard_stderr(stream):
    try:
        while stream.read(8192):
            pass
    except (OSError, ValueError):
        pass


def _write_job(stream, encoded, messages):
    try:
        remaining = memoryview(encoded)
        while remaining:
            written = stream.write(remaining)
            if not written:
                raise OSError("Native stdin write ended")
            remaining = remaining[written:]
        stream.flush()
    except (OSError, ValueError):
        messages.put(("write_error", None))


class NativeSession:
    """Serial persistent session; explicit close/context-manager is preferred.

    ``network_route='system'`` resolves the configured system route once unless
    an explicit proxy_url string was supplied (empty explicitly means direct).
    Proxy values remain constructor state and stdin data in RAM. Each Go HTTP
    call has its existing 20s timeout; the Python response deadline is <=25s.
    """

    def __init__(self, *, network_route="direct", proxy_url=None, timeout=25, max_requests=24,
                 executable=None):
        if network_route not in ("direct", "system", "proxy"):
            raise ValueError("Unknown native network route")
        if network_route == "direct" and proxy_url not in (None, ""):
            raise ValueError("A direct session cannot specify a proxy")
        if network_route == "system" and proxy_url is None:
            from urllib.request import getproxies, proxy_bypass
            try:
                resolved = {} if proxy_bypass("www.radissonhotels.com") else getproxies()
                proxy_url = resolved.get("https") or resolved.get("all") or resolved.get("http") or ""
            except Exception:
                raise ValueError("System proxy discovery failed") from None
        if network_route == "system" and not isinstance(proxy_url, str):
            raise ValueError("Invalid explicit system proxy shape")
        if network_route == "proxy" and (not isinstance(proxy_url, str) or not proxy_url):
            raise ValueError("An explicit proxy session requires its proxy string")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 25:
            raise ValueError("Native timeout must be within (0,25] seconds")
        if type(max_requests) is not int or not 1 <= max_requests <= 24:
            raise ValueError("Native request budget must be within 1..24")
        self.network_route = network_route
        self.timeout = float(timeout)
        self.max_requests = max_requests
        self.cookies = NativeCookieView()
        self._proxy_url = proxy_url or ""
        self._build_on_start = executable is None
        binary = "radisson-http.exe" if os.name == "nt" else "radisson-http"
        self._executable = Path(executable) if executable is not None else _HERE / "native_http" / binary
        self._messages = queue.Queue()
        self._threads = []
        self._process = None
        self._lock = threading.RLock()
        self._closed = False
        self._request_count = 0

    @property
    def request_count(self):
        return self._request_count

    @staticmethod
    def _target(method, url):
        if not isinstance(url, str):
            raise ValueError("Native URL must be a string")
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != "www.radissonhotels.com"
                or parsed.fragment or ".." in unquote(parsed.path).split("/")):
            raise ValueError("Request is outside the native helper scope")
        allowed = ((method == "GET" and (
            parsed.path == "/zh-cn/" or parsed.path.startswith(("/iFKu8e/", "/zimba-api/", "/content-api/hotels/"))
            or re.fullmatch(r"/content-api/currency/country/[A-Za-z]{2}", parsed.path)
            or parsed.path == "/availability-api/calendar-from")) or
            (method == "POST" and (parsed.path.startswith("/iFKu8e/") or parsed.path in (
                "/availability-api/stays-by-rooms", "/availability-api/multiple-hotels-rates"))))
        if not allowed:
            raise ValueError("Request is outside the native helper scope")

    def _start(self):
        if self._closed:
            raise NativeTransportError("Native session is closed")
        if self._process is not None:
            if self._process.poll() is not None:
                self._close_locked(abort=True)
                raise NativeTransportError("Native helper exited")
            return
        if self._build_on_start:
            _build_backend(self._executable)
        elif not self._executable.is_file():
            raise NativeTransportError("The native helper is missing")
        try:
            self._process = subprocess.Popen(
                [str(self._executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=65536, cwd=str(_HERE),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError:
            self._closed = True
            raise NativeTransportError("Native helper could not start") from None
        for target, args in ((_read_stdout, (self._process.stdout, self._messages)),
                             (_discard_stderr, (self._process.stderr,))):
            thread = threading.Thread(target=target, args=args, daemon=True)
            thread.start()
            self._threads.append(thread)

    def request(self, method, url, *, headers=None, data=None, json=_UNSET,
                timeout=None, allow_redirects=False):
        method = str(method).upper()
        self._target(method, url)
        if allow_redirects is not False:
            raise ValueError("Native redirects must remain disabled")
        deadline_seconds = self.timeout if timeout is None else timeout
        if isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float)) or not 0 < deadline_seconds <= 25:
            raise ValueError("Native timeout must be within (0,25] seconds")
        if json is not _UNSET and data is not None:
            raise ValueError("Use data or json, not both")
        if json is not _UNSET:
            body = json_module.dumps(json, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        elif data is None:
            body = ""
        elif isinstance(data, bytes):
            body = data.decode("utf-8")
        elif isinstance(data, str):
            body = data
        else:
            raise TypeError("Native data must be UTF-8 bytes or text")
        if len(body.encode("utf-8")) > 65536:
            raise ValueError("Native request body exceeds the helper limit")
        items = headers.items() if isinstance(headers, Mapping) else headers or ()
        pairs = []
        for name, value in items:
            if not isinstance(name, str) or not isinstance(value, str) or not _HEADER_NAME.fullmatch(name) or "\r" in value or "\n" in value:
                raise ValueError("Invalid native request header shape")
            if name.lower() not in _SKIP_HEADERS:
                pairs.append([name, value])
        if json is not _UNSET and not any(name.lower() == "content-type" for name, _ in pairs):
            pairs.append(["Content-Type", "application/json"])
        if len(pairs) > 64:
            raise ValueError("Native header count exceeds the helper limit")
        job = {"method": method, "url": url, "headers": pairs, "body": body, "proxy_url": self._proxy_url}
        encoded = (json_module.dumps(job, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            if self._closed:
                raise NativeTransportError("Native session is closed")
            if self._request_count >= self.max_requests:
                raise NativeTransportError("Native request budget exhausted")
            self._start()
            if not self._messages.empty():
                self._close_locked(abort=True)
                raise NativeProtocolError("Unsolicited native helper output")
            deadline = time.monotonic() + float(deadline_seconds)
            self._request_count += 1
            writer = threading.Thread(target=_write_job, args=(self._process.stdin, encoded, self._messages), daemon=True)
            writer.start()
            self._threads.append(writer)
            try:
                kind, line = self._messages.get(timeout=max(0.001, deadline - time.monotonic()))
            except queue.Empty:
                self._close_locked(abort=True)
                raise NativeTimeoutError("Native response deadline exceeded; session closed") from None
            if kind != "line":
                self._close_locked(abort=True)
                raise NativeProtocolError("Native helper stream ended or was invalid")
            try:
                envelope = json_module.loads(line)
                line = None
                if not isinstance(envelope, dict):
                    raise NativeProtocolError("Invalid native response envelope")
                error_type = envelope.get("error_type")
                if error_type:
                    if error_type not in _KNOWN_ERRORS:
                        raise NativeProtocolError("Unknown native helper error category")
                    raise NativeTransportError(error_type)
                response = NativeResponse(envelope, url=url, method=method)
            except (ValueError, TypeError, NativeProtocolError):
                self._close_locked(abort=True)
                raise NativeProtocolError("Native response protocol validation failed") from None
            finally:
                writer.join(timeout=0.2)
            self.cookies = response.cookies
            return response

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def _close_locked(self, *, abort=False):
        if self._closed and self._process is None:
            return
        self._closed = True
        process, self._process = self._process, None
        if process is not None:
            if abort and process.poll() is None:
                process.terminate()
            if not abort and process.stdin:
                try:
                    process.stdin.close()
                except (OSError, ValueError):
                    pass
            try:
                process.wait(timeout=0.8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.8)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe:
                    try:
                        pipe.close()
                    except (OSError, ValueError):
                        pass
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=0.2)
        self._threads.clear()
        self.cookies = NativeCookieView()
        while True:
            try:
                self._messages.get_nowait()
            except queue.Empty:
                break

    def close(self):
        with self._lock:
            self._close_locked()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def __repr__(self):
        return f"<NativeSession route={self.network_route!r} requests={self._request_count} closed={self._closed}>"
