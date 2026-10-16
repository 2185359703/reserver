from datetime import date

from _common.client import RequestSpec, path_value
from _common.context import currency_headers, source, taxes
from _common import parameters as p


def build_request(config):
    path_value(config.get("hotel_code"))
    today = p.local_today()
    check_in, check_out = p.normalize_dates(config, today)
    start = max(date.fromisoformat(config.get("start_check_date") or check_in), today)
    profile = config.get("profile") or {}
    # is_pcr/is_tmc are already evaluated account state, not fabricated IDs.
    special = config.get("is_pcr", False) or config.get("is_tmc", False)
    rate = config.get("search_type", "lowest")
    search_type = ("travelAgency" if profile.get("isTravelAgent") else "corporateAccount") if special else ("member" if rate == "lowest" and config.get("only_members") else rate)
    promotion = config.get("promotion_code")
    if not p.js_truthy(promotion):
        promotion = ((profile.get("companyData") or {}).get("companyId") or "") if special else ""
    occupancy = p.calendar_occupancy_fields(p.make_occupancies(config))
    params = {
        "hotelCode": config["hotel_code"],
        "startCheckDate": start.isoformat(),
        "startSelectDate": config.get("start_select_date", check_in),
        "nights": max(1, (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days),
        "adults": occupancy["adults"], "rooms": occupancy["rooms"],
        "searchType": search_type, "promotionCode": promotion,
        "futureDays": p.calendar_horizon(start, today),
        "origin": config.get("origin", p.UNDEFINED),
        "sourceOfBusiness": source(config),
        "taxIncludedRegion": taxes(config, calendar=True),
        "memberType": (profile.get("memberTypeExtended") or {}).get("code") or "",
    }
    if "children" in occupancy:
        params.update({"children": occupancy["children"], "aoc": occupancy["aoc"]})
    return RequestSpec("GET", "/availability-api/calendar-from", params=p.omit_undefined(params),
                       headers=currency_headers(config), query_encode=False,
                       required_response_fields=("dates",))


def fetch(client, config):
    resolved = client.resolve_context(config, modern_currency=True)
    return client.send(build_request(resolved))
