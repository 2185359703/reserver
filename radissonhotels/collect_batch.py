"""直接运行：跨城市采集酒店、三组房价及详情，结果写入 data/。"""
from __future__ import annotations

import csv
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _common import parameters as p
from _common.client import ApiError, Client, PUBLIC_CONFIG_KEYS, RequestSpec, redact
from autocomplete.request import build_request as autocomplete
from calendar_from.request import build_request as calendar
from hotel_cards.request import build_request as cards
from hotel_details.request import build_request as details
from hotel_rooms.request import build_request as rooms
from hotel_search.request import build_request as search
from multiple_hotels_rates.request import build_request as rates
from stays_by_rooms.request import build_request as stays

ROOT = Path(__file__).resolve().parent


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class BatchStop(RuntimeError):
    pass


def write_json(path, data):
    path.write_text(json.dumps(redact(data), ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


class Batch:
    def __init__(self, config):
        self.config = config
        self.base = {key: config[key] for key in ("transport", "network_route", "preferred_currency", "viewport_width", "timeout") if key in config}
        for scenario in config["scenarios"]:
            if p.normalize_dates(scenario) != (scenario["check_in"], scenario["check_out"]):
                raise ValueError("Scenario dates are not current valid frontend inputs")
            p.make_occupancies(scenario)
        stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S-%f")
        self.output = ROOT / "data" / ("batch-"+stamp)
        (self.output / "responses").mkdir(parents=True)
        self.client = None
        self.session_count = 0
        self.session_stats = []
        self.records = []
        self.row_counts = Counter()
        self.last_started = 0.0
        self.access_failures = 0
        self.dynamic = {}
        self.catalog = {}
        self.city_rows = []
        self.selected = []
        self.city_by_code = {}
        self.summary = {"started_at_utc": utc_now(), "status": "running", "output": str(self.output),
                        "browser_used": False, "browser_state_imported": False, "cookie_values_saved": False,
                        "dependency_snapshot_used": False, "input": config, "discovery": [], "rate_batches": []}
        write_json(self.output / "manifest.json", self.summary)

    def append(self, name, data):
        with (self.output / (name+".jsonl")).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(redact(data), ensure_ascii=False, separators=(",", ":"))+"\n")
        self.row_counts[name] += 1

    def close_session(self):
        if self.client is not None:
            self.session_stats.append({"session": self.session_count, "requests": len(self.client.history),
                                       "accepted": sum(row["accepted"] for row in self.client.history)})
            self.client.close()
            self.client = None

    def session(self):
        # Each packaged helper accepts 24 requests. End the current session
        # before that boundary; the next one starts with its own empty jar.
        if self.client is None or len(self.client.history) >= 22:
            self.close_session()
            self.client = Client(self.base)
            self.session_count += 1
        return self.client

    def inputs(self, extra=None):
        client = self.session()
        return {**self.base, "dynamic_config": self.dynamic, "device_country": client.device_country(),
                "profile": None, **(extra or {})}

    def send(self, spec, kind, context=None, schema=None, *, save=True):
        if len(self.records) >= self.config["max_requests"]:
            raise BatchStop("Configured request count reached")
        client = self.session()
        wait = self.config["request_interval_seconds"] - (time.monotonic()-self.last_started)
        if wait > 0:
            time.sleep(wait)
        self.last_started = time.monotonic()
        sequence = len(self.records)+1
        record = {"sequence": sequence, "session": self.session_count, "endpoint": kind,
                  "context": context or {}, "started_at_utc": utc_now(), "accepted": False}
        data = None
        try:
            data = client.send(spec)
            record.update(client.last_evidence)
            if not isinstance(data, dict) or not all(type(data.get(key)) is expected for key, expected in (schema or {}).items()):
                record.update(accepted=False, error="unexpected_business_schema")
                data = None
            else:
                record["business_counts"] = {key: len(data[key]) for key in schema or {} if isinstance(data[key], (dict, list))}
                if save:
                    filename = f"{sequence:04d}_{kind}.json"
                    write_json(self.output / "responses" / filename, data)
                    record["response_file"] = "responses/"+filename
        except ApiError:
            record.update(client.last_evidence)
        except Exception as exc:
            record.update(error_type=type(exc).__name__, error="request_failed")
        record["elapsed_seconds"] = round(time.monotonic()-self.last_started, 3)
        self.records.append(record)
        self.append("requests", record)
        if not record["accepted"]:
            self.append("failures", record)
        self.access_failures = self.access_failures+1 if record.get("status") in (403, 429) else 0
        print(json.dumps({key: record.get(key) for key in ("sequence", "session", "endpoint", "context", "status", "accepted")}, ensure_ascii=False), flush=True)
        if self.access_failures >= 2:
            raise BatchStop("Consecutive access-denied responses; partial results retained")
        return data, sequence

    def discover(self):
        data, sequence = self.send(RequestSpec("GET", "/zimba-api/config-dynamic", required_response_fields=("keys",)),
                                   "config_dynamic", schema={"keys": dict}, save=False)
        if not data:
            raise BatchStop("Current public configuration unavailable")
        self.dynamic = {"keys": {key: data["keys"][key] for key in PUBLIC_CONFIG_KEYS if key in data["keys"]}}
        write_json(self.output / "responses" / f"{sequence:04d}_config_dynamic_public_subset.json", self.dynamic)
        data, _ = self.send(RequestSpec("GET", "/zimba-api/search-result/categories/hotels", required_response_fields=("hotels",)),
                            "categories", schema={"hotels": list})
        if not data:
            raise BatchStop("Current public hotel categories unavailable")
        self.catalog = {row["code"]: row for row in data["hotels"] if isinstance(row, dict) and row.get("code")}
        batches = p.availability_batches(list(self.catalog.values()), self.dynamic)
        eligible = {row["code"] for batch in batches for row in batch["hotels"]}
        for query in self.config["queries"]:
            result, _ = self.send(autocomplete(self.inputs({"text": query})), "autocomplete", {"query": query},
                                  {"places": list, "hotels": list})
            if not result:
                continue
            def score(place):
                types = place.get("types") or []
                name = str(place.get("name", "")).casefold()
                return (100 if "locality" in types else 50 if "country" in types else 0) + (40 if query.casefold() in name else 0)
            candidates = sorted((row for row in result["places"] if row.get("placeId") or row.get("place_id")), key=score, reverse=True)
            if not candidates:
                self.summary["discovery"].append({"query": query, "error": "no_public_place_id"})
                continue
            place = candidates[0]
            place_id = place.get("placeId") or place["place_id"]
            result, _ = self.send(search(self.inputs({"place_id": place_id})), "hotel_search",
                                  {"query": query, "place_id": place_id}, {"hotels": list})
            if not result:
                continue
            codes = list(dict.fromkeys(row["code"] for row in result["hotels"] if isinstance(row, dict) and row.get("code") in eligible))
            item = {"query": query, "place_name": place.get("name"), "place_id": place_id,
                    "found_hotels": len(result["hotels"]), "eligible_hotels": len(codes), "codes": codes}
            self.city_rows.append(item)
            self.summary["discovery"].append(item)
            self.append("cities", item)
        # Round-robin selection gives every discovered city representation.
        for index in range(max((len(row["codes"]) for row in self.city_rows), default=0)):
            for city in self.city_rows:
                if index >= len(city["codes"]):
                    continue
                code = city["codes"][index]
                if code not in self.city_by_code:
                    self.selected.append(code)
                    self.city_by_code[code] = city["query"]
                if len(self.selected) >= self.config["max_hotels"]:
                    break
            if len(self.selected) >= self.config["max_hotels"]:
                break
        if not self.selected:
            raise BatchStop("No eligible hotel codes found through current public searches")
        self.summary["selected_hotels"] = len(self.selected)
        self.summary["selected_by_city"] = dict(Counter(self.city_by_code.values()))

    def collect_cards(self):
        size = self.config.get("cards_batch_size", 20)
        for start in range(0, len(self.selected), size):
            codes = self.selected[start:start+size]
            data, sequence = self.send(cards(self.inputs({"hotel_codes": codes})), "hotel_cards", {"hotel_count": len(codes)}, {"hotels": list})
            if not data:
                continue
            returned = set()
            for hotel in data["hotels"]:
                code = hotel.get("code")
                returned.add(code)
                self.append("hotels", {"hotel_code": code, "query_city": self.city_by_code.get(code),
                                       "name": hotel.get("name"), "brand": hotel.get("brand"), "stars": hotel.get("stars"),
                                       "source_request": sequence, "data": hotel})
            if set(codes)-returned:
                self.append("coverage", {"endpoint": "hotel_cards", "source_request": sequence,
                                          "missing_hotel_codes": sorted(set(codes)-returned)})

    def collect_rates(self):
        categories = [self.catalog[code] for code in self.selected]
        for scenario in self.config["scenarios"]:
            self.close_session()
            config = self.inputs({**scenario, "hotel_codes": self.selected, "category_hotels": categories})
            specs = rates(config)
            self.summary["rate_batches"].append({"scenario": scenario["name"], "batch_sizes": [len(spec.body["hotels"]) for spec in specs]})
            for batch_index, spec in enumerate(specs):
                expected = {row["code"] for row in spec.body["hotels"]}
                data, sequence = self.send(spec, "multiple_hotels_rates", {"scenario": scenario["name"], "batch": batch_index, "hotel_count": len(expected)}, {"hotels": list})
                if not data:
                    continue
                returned = set()
                for hotel in data["hotels"]:
                    code = hotel.get("code")
                    returned.add(code)
                    cash = hotel.get("minimumCash") or {}
                    average = cash.get("averagePricing") or {}
                    price = average.get("price") or {}
                    total = cash.get("totalPriceWithTax") or {}
                    self.append("prices", {"hotel_code": code, "query_city": self.city_by_code.get(code),
                        "scenario": scenario["name"], "check_in": scenario["check_in"], "check_out": scenario["check_out"],
                        "occupancy": p.make_occupancies(scenario), "currency": price.get("currency"),
                        "requested_currency": config.get("preferred_currency"), "average_price": price.get("amount"),
                        "total_price_with_tax": total.get("amount"), "rooms_left": cash.get("numberOfRoomsLeft"),
                        "search_applicable": hotel.get("searchApplicable"), "has_cash_rate": bool(hotel.get("minimumCash")),
                        "source_request": sequence, "data": hotel})
                if expected-returned:
                    self.append("coverage", {"endpoint": "multiple_hotels_rates", "scenario": scenario["name"],
                                              "source_request": sequence, "missing_hotel_codes": sorted(expected-returned)})

    def collect_details(self):
        for index, code in enumerate(self.selected[:self.config["detail_hotels"]]):
            self.close_session()
            scenario = self.config["scenarios"][index % len(self.config["scenarios"])]
            config = self.inputs({**scenario, "hotel_code": code, "search_type": "lowest"})
            context = {"hotel_code": code, "query_city": self.city_by_code[code], "scenario": scenario["name"]}
            data, sequence = self.send(details(config), "hotel_details", context, {"hotel": dict})
            if data:
                self.append("details", {**context, "source_request": sequence, "data": data["hotel"]})
            definitions, sequence = self.send(rooms(config), "hotel_rooms", context, {"rooms": list})
            if definitions:
                for room in definitions["rooms"]:
                    self.append("rooms", {**context, "source_request": sequence, "data": room})
            if definitions and definitions["rooms"]:
                config = self.inputs({**config, "rooms": definitions["rooms"]})
                data, sequence = self.send(stays(config), "stays_by_rooms", context, {"rooms": dict})
                if data:
                    for room_id, offer in data["rooms"].items():
                        self.append("room_prices", {**context, "check_in": scenario["check_in"], "check_out": scenario["check_out"],
                            "room_id": room_id, "currency": data.get("currency"), "source_request": sequence, "data": offer})
            data, sequence = self.send(calendar(self.inputs(config)), "calendar_from", context, {"dates": list})
            if data:
                for day in data["dates"]:
                    self.append("calendar_prices", {**context, "source_request": sequence, "data": day})

    def export_csv(self):
        for name, fields in {
            "hotels": ["hotel_code", "query_city", "name", "brand", "stars", "source_request"],
            "prices": ["hotel_code", "query_city", "scenario", "check_in", "check_out", "currency", "requested_currency",
                       "average_price", "total_price_with_tax", "rooms_left", "search_applicable", "has_cash_rate", "source_request"],
        }.items():
            source = self.output / (name+".jsonl")
            if not source.exists():
                continue
            with source.open(encoding="utf-8") as src, (self.output / (name+".csv")).open("w", encoding="utf-8-sig", newline="") as dest:
                writer = csv.DictWriter(dest, fields, extrasaction="ignore")
                writer.writeheader()
                for line in src:
                    writer.writerow(json.loads(line))
        source = self.output / "calendar_prices.jsonl"
        if source.exists():
            fields = ["hotel_code", "query_city", "scenario", "date", "amount", "currency", "lowest", "min_nights_per_stay", "source_request"]
            with source.open(encoding="utf-8") as src, (self.output / "calendar_prices.csv").open("w", encoding="utf-8-sig", newline="") as dest:
                writer = csv.DictWriter(dest, fields)
                writer.writeheader()
                for line in src:
                    row = json.loads(line)
                    day = row["data"]
                    price = day.get("price") or {}
                    writer.writerow({"hotel_code": row["hotel_code"], "query_city": row["query_city"], "scenario": row["scenario"],
                        "date": day.get("date"), "amount": price.get("amount"), "currency": price.get("currency"),
                        "lowest": day.get("lowest"), "min_nights_per_stay": day.get("minNightsPerStay"), "source_request": row["source_request"]})

    def finish(self, status, reason=None):
        self.close_session()
        self.export_csv()
        stats = {}
        for row in self.records:
            entry = stats.setdefault(row["endpoint"], {"attempts": 0, "accepted": 0, "statuses": {}})
            entry["attempts"] += 1
            entry["accepted"] += bool(row["accepted"])
            key = str(row.get("status", "transport_error"))
            entry["statuses"][key] = entry["statuses"].get(key, 0)+1
        self.summary.update(status=status, finished_at_utc=utc_now(), reason=reason,
                            http_requests=len(self.records), http_accepted=sum(row["accepted"] for row in self.records),
                            endpoint_stats=stats, row_counts=dict(self.row_counts), sessions=self.session_stats)
        write_json(self.output / "manifest.json", self.summary)
        write_json(ROOT / "data/latest-batch-run.json", self.summary)
        print(json.dumps({"status": status, "requests": len(self.records), "rows": dict(self.row_counts), "output": str(self.output)}, ensure_ascii=False), flush=True)


def main():
    config = json.loads((ROOT / "batch_config.json").read_text(encoding="utf-8-sig"))
    batch = Batch(config)
    status, reason = "completed", None
    try:
        batch.discover()
        batch.collect_cards()
        batch.collect_rates()
        batch.collect_details()
    except BatchStop as exc:
        status, reason = "stopped", str(exc)
    except Exception as exc:
        status, reason = "failed", type(exc).__name__
    finally:
        batch.finish(status, reason)
    return 0 if status == "completed" and all(row["accepted"] for row in batch.records) else 2


if __name__ == "__main__":
    raise SystemExit(main())
