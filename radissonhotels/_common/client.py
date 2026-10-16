"""Browser-free transport and runnable entry shared by the eight APIs."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode

from _common import parameters as p

BASE_URL = "https://www.radissonhotels.com"
PUBLIC_CONFIG_KEYS = ("default.taxes.included.country.codes", "default.taxes.excluded.country.codes", "booking.sr.hotels.avail.request.limit")


class ApiError(RuntimeError):
    def __init__(self, message, evidence):
        super().__init__(message)
        self.evidence = evidence


@dataclass
class RequestSpec:
    method: str
    path: str
    params: dict[str, Any] = field(default_factory=dict)
    body: dict[str, Any] | None = None
    headers: dict[str, Any] = field(default_factory=dict)
    style: str = "modern"
    transaction: bool = True
    required_response_fields: tuple[str, ...] = ()
    query_encode: bool = True

    def url(self):
        # Calendar uses qs.stringify(arrayFormat='comma', encode=false).
        values = [(key, "" if value is None else p.js_string(value))
                  for key, value in self.params.items() if value is not p.UNDEFINED]
        query = urlencode(values) if self.query_encode else "&".join(f"{key}={value}" for key, value in values)
        return BASE_URL + self.path + ("?" + query if query else "")

    def describe(self):
        return {"method": self.method, "url": self.url(), "headers": p.omit_undefined(self.headers),
                "body": p.omit_undefined(self.body), "style": self.style,
                "transaction_id": "40 base15 characters, generated once and reused per client" if self.transaction else None}


def path_value(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Path parameter must be a nonempty string")
    return quote(value, safe="~!*'()-._")  # encodeURIComponent


def redact(value):
    sensitive = {"authorization", "cookie", "set-cookie", "password", "secret", "sessionid", "access_token",
                 "refresh_token", "token", "rewardsnumber", "loyaltynumberr4u", "iatanumberr4u",
                 "customerid", "firstname", "lastname", "email", "transactionid"}
    if isinstance(value, dict):
        return {key: ("[redacted]" if str(key).lower() in sensitive and item is not None else redact(item))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class Client:
    def __init__(self, config, *, offline=False):
        self.config = config
        self.transaction_id = p.transaction_id()
        self.transport = config.get("transport", "native")
        self.network_route = config.get("network_route", "direct")
        if self.network_route not in ("direct", "system", "proxy"):
            raise ValueError("network_route must be direct, system or proxy")
        self.history = []
        self.last_evidence = {}
        self.session = None
        if offline:
            return
        if self.transport == "native":
            from _common.native_transport import NativeSession
            self.session = NativeSession(network_route=self.network_route,
                                         proxy_url=config.get("proxy_url"),
                                         timeout=float(config.get("timeout", 25)))
        else:
            raise ValueError("transport must be native")

    def headers_for(self, spec):
        from _common.context import http_headers, source
        page = "search-results" if spec.style == "modern" else "room-display"
        page_url = self.config.get("page_url", f"{BASE_URL}/zh-cn/booking/{page}")
        headers = {"Accept-Language": p.language_from_page(page_url),
                   "Accept": "*/*" if spec.style == "modern" else "application/json, text/plain, */*",
                   "Referer": page_url, **http_headers(self.config)}
        if spec.style == "modern":
            headers.update({"Content-Type": "application/json", "X-SOURCE-OF-BUSINESS": source(self.config)})
        else:
            headers["Accept"] = "application/json, text/plain, */*"
            if spec.body is not None:
                headers["Content-Type"] = "application/json"
        # Match the observed page origin. Controlled comparisons also
        # succeeded without this header; it does not establish admission.
        headers["Origin"] = BASE_URL
        if spec.transaction:
            headers["Transactionid"] = self.transaction_id
        headers.update({key: p.js_string(value) for key, value in spec.headers.items() if p.js_truthy(value)})
        return headers

    def device_country(self):
        """Read only country from this session's server-issued device cookie."""
        if self.session is None:
            return None
        jar = getattr(self.session.cookies, "jar", self.session.cookies)
        for cookie in jar:
            if cookie.name == "rhg-device-info":
                raw = unquote(cookie.value).strip('"')
                country = parse_qs(raw).get("country", [None])[0]
                if isinstance(country, str) and len(country) == 2 and country.isalpha():
                    return country
        return None

    def send(self, spec):
        allowed = ("/zimba-api/", "/content-api/hotels/", "/content-api/currency/country/", "/availability-api/")
        if not spec.path.startswith(allowed) or self.session is None:
            raise ValueError("Unexpected API path or offline client")
        kwargs = {"headers": self.headers_for(spec), "timeout": float(self.config.get("timeout", 25)), "allow_redirects": False}
        if spec.body is not None:
            kwargs["data"] = json.dumps(p.omit_undefined(spec.body), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        evidence = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "method": spec.method,
                    "path": spec.path, "transport": self.transport, "network_route": self.network_route,
                    "browser_state_imported": False, "accepted": False}
        self.last_evidence = evidence
        self.history.append(evidence)
        try:
            response = self.session.request(spec.method, spec.url(), **kwargs)
        except Exception as exc:
            evidence["error"] = type(exc).__name__
            raise ApiError("HTTP transport failed", evidence) from exc
        evidence.update(status=response.status_code, content_type=response.headers.get("content-type", ""), bytes=len(response.content))
        if response.status_code != 200:
            evidence["error"] = "access_denied" if response.status_code in (401, 403, 429) else "http_error"
            raise ApiError(f"HTTP {response.status_code} for {spec.path}", evidence)
        try:
            data = response.json()
        except ValueError as exc:
            evidence["error"] = "non_json_response"
            raise ApiError("The server returned non-JSON content", evidence) from exc
        if not isinstance(data, (dict, list)):
            evidence["error"] = "unexpected_json_type"
            raise ApiError("Expected a JSON object or array", evidence)
        if spec.required_response_fields and (not isinstance(data, dict) or any(key not in data for key in spec.required_response_fields)):
            evidence["error"] = "missing_business_fields"
            raise ApiError("Response is missing the observed business fields", evidence)
        evidence["accepted"] = True
        evidence["response_fields"] = list(data) if isinstance(data, dict) else None
        return data

    def resolve_context(self, config, *, categories=False, rooms=False, modern_currency=False):
        """Fetch public dependencies through this same HTTP session."""
        result = dict(config)
        dynamic = self.send(RequestSpec("GET", "/zimba-api/config-dynamic", required_response_fields=("keys",)))
        result["dynamic_config"] = {"keys": {key: dynamic["keys"][key] for key in PUBLIC_CONFIG_KEYS if key in dynamic["keys"]}}
        if not result.get("device_country"):
            result["device_country"] = self.device_country()
        if modern_currency and not p.js_truthy(result.get("preferred_currency", p.UNDEFINED)) and result.get("device_country"):
            try:
                result["currency_response"] = self.send(RequestSpec("GET", "/content-api/currency/country/" + path_value(result["device_country"])))
            except ApiError:
                # W_ catches this auxiliary failure and the wrapper omits the
                # falsy X-Currency. The failed attempt remains in history.
                result["currency_response"] = None
                result.setdefault("context_warnings", []).append("country currency lookup failed; X-Currency omitted as in W_")
        if categories:
            if not result.get("hotel_codes") and result.get("place_id"):
                from hotel_search.request import build_request
                search = self.send(build_request(result))
                result["hotel_codes"] = [hotel["code"] for hotel in search["hotels"]]
            response = self.send(RequestSpec("GET", "/zimba-api/search-result/categories/hotels", required_response_fields=("hotels",)))
            selected = set(result.get("hotel_codes", []))
            result["category_hotels"] = [hotel for hotel in response["hotels"] if hotel.get("code") in selected]
        if rooms:
            from hotel_rooms.request import build_request
            response = self.send(build_request(result))
            result["rooms"] = response["rooms"]
        result["context_provenance"] = "fresh public APIs via this browser-free HTTP session"
        return result

    def close(self):
        if self.session is not None:
            self.session.close()


def load_config(folder, *, mode=None):
    config = json.loads((folder / "config.json").read_text(encoding="utf-8-sig"))
    if mode:
        config["mode"] = mode
    if config.get("mode", "build") == "build" and config.get("dependency_snapshot"):
        path = (folder / config["dependency_snapshot"]).resolve()
        snapshot = json.loads(path.read_text(encoding="utf-8-sig"))
        config.setdefault("dynamic_config", snapshot["dynamic_config"])
        config.setdefault("category_hotels", snapshot["categories"]["hotels"])
        if config.get("hotel_code") == "CNJSWUXWET":
            config.setdefault("rooms", snapshot["rooms"]["rooms"])
        config["context_provenance"] = {"file": str(path), "checked_at_utc": snapshot["checked_at_utc"], "mode": "offline public dependency example"}
    return config


def run_endpoint(folder, build_request, fetch=None):
    parser = argparse.ArgumentParser(description="Reconstruct parameters from the saved frontend source")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--build", action="store_true", help="Build using explicit inputs and the public dependency example")
    group.add_argument("--live", action="store_true", help="Use a fresh HTTP session, fetch public dependencies and send")
    args = parser.parse_args()
    return execute_endpoint(folder, build_request, fetch,
                            mode="build" if args.build else "live" if args.live else None)


def execute_endpoint(folder, build_request, fetch=None, *, mode=None):
    """Run one endpoint with its own session, also used by the batch entry."""
    config = load_config(folder, mode=mode)
    mode = config.get("mode", "build")
    if mode not in ("build", "live"):
        raise ValueError("mode must be build or live")
    client = Client(config, offline=mode == "build")
    accepted = False
    try:
        if mode == "build":
            specs = build_request(config)
            specs = specs if isinstance(specs, list) else [specs]
            prepared = {"mode": "build", "sent": False, "context_provenance": config.get("context_provenance", "explicit config inputs"),
                        "requests": [dict(spec.describe(), headers=client.headers_for(spec)) for spec in specs]}
            prepared = redact(prepared)
            (folder / "prepared_request.json").write_text(json.dumps(prepared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(prepared, ensure_ascii=False, indent=2))
            return 0
        data = fetch(client, config) if fetch else client.send(build_request(config))
        (folder / "response.json").write_text(json.dumps(redact(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        accepted = True
        print(json.dumps({"mode": "live", "accepted": True, "requests": client.history}, ensure_ascii=False, indent=2))
        return 0
    except ApiError as exc:
        print(json.dumps({"mode": mode, "accepted": False, "error": str(exc), "requests": client.history}, ensure_ascii=False, indent=2))
        return 2
    except (ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"mode": mode, "accepted": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    finally:
        if mode == "live":
            evidence = {"mode": "live", "accepted": accepted, "requests": client.history}
            (folder / "last_run.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        client.close()
