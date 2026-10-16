"""Pure business calculations recovered from the saved Radisson JavaScript.

Identifiers, geography and configuration are inputs from the UI/public APIs.
No helper here reads a browser or manufactures server-issued identifiers.
UNDEFINED is distinct from None: JSON.stringify omits object properties with
undefined values, while it retains null and false.
"""
from __future__ import annotations

import math
import re
import secrets
from datetime import date, datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlsplit


class _Undefined:
    def __repr__(self):
        return "undefined"

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


UNDEFINED = _Undefined()
# ECMAScript WhiteSpace + LineTerminator; Python str.strip additionally removes
# characters such as U+0085 which JavaScript trim deliberately retains.
_JS_WHITESPACE = "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


def js_truthy(value: Any) -> bool:
    if value is UNDEFINED or value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0 and not (isinstance(value, float) and math.isnan(value))
    if isinstance(value, str):
        return len(value) > 0
    return True  # JavaScript empty objects and arrays are truthy.


def js_string(value: Any) -> str:
    """String(value) for the JSON-compatible parameter values used by the site."""
    if value is UNDEFINED:
        return "undefined"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return _js_join(value)
    if isinstance(value, dict):
        return "[object Object]"
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        if value == int(value):
            return str(int(value))
    return str(value)


def _js_join(values, separator=","):
    return separator.join("" if item is None or item is UNDEFINED else js_string(item) for item in values)


def js_parse_int(value: Any, radix: int | None = None) -> int | float:
    """parseInt used for URL occupancy and the dynamic batch limit (NaN on failure)."""
    text = js_string(value).lstrip(_JS_WHITESPACE)
    sign = -1 if text.startswith("-") else 1
    if text[:1] in ("+", "-"):
        text = text[1:]
    base = radix or (16 if text[:2].lower() == "0x" else 10)
    if base == 16 and text[:2].lower() == "0x":
        text = text[2:]
    if not 2 <= base <= 36:
        return math.nan
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"[:base]
    digits = []
    for char in text.lower():
        if char not in alphabet:
            break
        digits.append(char)
    return sign * int("".join(digits), base) if digits else math.nan


def omit_undefined(value: Any) -> Any:
    """JSON.stringify-compatible cleanup, including NaN/Infinity -> null."""
    if isinstance(value, dict):
        return {key: omit_undefined(item) for key, item in value.items() if item is not UNDEFINED}
    if isinstance(value, (list, tuple)):
        return [None if item is UNDEFINED else omit_undefined(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def transaction_id(length: int = 40, random_source: Callable[[], float] | None = None) -> str:
    """$F/Z_: floor(Math.random()*15).toString(15), repeated 40 times.

    The injectable source allows deterministic comparison to the JavaScript.
    A client should reuse the result for its session, as sessionStorage does.
    """
    random_source = random_source or secrets.SystemRandom().random
    alphabet = "0123456789abcde"
    result = []
    for _ in range(length):
        value = random_source()
        if not 0 <= value < 1:
            raise ValueError("The Math.random-compatible source must return 0 <= x < 1")
        result.append(alphabet[math.floor(value * 15)])
    return "".join(result)


def language_from_page(page_url: str, fallback: str = "en-us") -> str:
    parts = urlsplit(page_url).path.split("/")
    return ((parts[1] if len(parts) > 1 else "") or fallback or "en-us").lower()


def source_of_business(viewport_width: float) -> str:
    # jr() uses matchMedia('(max-width: 767px)'), not the User-Agent.
    return "mobile" if float(viewport_width) <= 767 else "desktop"


def _country_list_contains(country: Any, config: dict[str, Any], key: str) -> bool:
    value = config.get("keys", config).get(key, UNDEFINED)
    if value is None or value is UNDEFINED:
        return False
    if not isinstance(value, str):
        raise TypeError("The source configuration country lists must be strings")
    return country in value.split(",")  # No trim or case conversion in HF/EF.


def tax_region(country: Any, dynamic_config: dict[str, Any]) -> bool | _Undefined:
    """pR: included -> true, excluded -> false, neither -> undefined."""
    if js_truthy(country) and _country_list_contains(country, dynamic_config, "default.taxes.included.country.codes"):
        return True
    if js_truthy(country) and _country_list_contains(country, dynamic_config, "default.taxes.excluded.country.codes"):
        return False
    return UNDEFINED


def calendar_tax_region(country: Any, dynamic_config: dict[str, Any]) -> bool | _Undefined:
    """Calendar M=EF()?false:HF()||undefined; excluded takes precedence."""
    if js_truthy(country) and _country_list_contains(country, dynamic_config, "default.taxes.excluded.country.codes"):
        return False
    return tax_region(country, dynamic_config)


def currency(preferred: Any = UNDEFINED, country_response: dict[str, Any] | None = None, *, legacy: bool = False):
    """W_: preferred currency, otherwise the public country API's currency.

    None means the modern lookup could not run/failed. A supplied response with
    no currency returns UNDEFINED, exactly as optional chaining in W_ does.
    Legacy getStays only uses preferred || 'EUR'; it never calls that API.
    """
    if js_truthy(preferred):
        return preferred
    if legacy:
        return "EUR"
    return country_response.get("currency", UNDEFINED) if country_response is not None else None


def exclude_member_rates(customer: Any = None, loyalty_consent: Any = None, model_excludes: bool = False) -> bool:
    # S = customer || !!consent ? false : modelExcludes || !customer
    return False if js_truthy(customer) or js_truthy(loyalty_consent) else bool(model_excludes or not js_truthy(customer))


def _js_or(value: Any, fallback: Any) -> Any:
    return value if js_truthy(value) else fallback


def _nested_get(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, dict):
            return UNDEFINED
        value = value.get(key, UNDEFINED)
    return value


def _object_spread(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (list, tuple, str)):
        return {str(index): item for index, item in enumerate(value)}
    return {}


def customer_payload(profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """roomDisplay Customer constructor (not Customer.fromJSON)."""
    profile = {} if profile is None else profile
    rewards = _js_or(profile.get("rewardsNumber", UNDEFINED), None)
    tier = _js_or(profile.get("tier", UNDEFINED), None)
    user = profile.get("user", UNDEFINED)
    if js_truthy(user):
        user = dict(_object_spread(user), memberTypes=_js_or(_nested_get(user, "memberTypes"), []))
    anonymous = not js_truthy(rewards) and not js_truthy(tier)
    result = {
        "crowd": _js_or(profile.get("crowd", UNDEFINED), None),
        "rewardsNumber": rewards,
        "points": _js_or(profile.get("points", UNDEFINED), 0),
        "tier": tier,
        "isAnonymous": anonymous,
        "isLogged": not anonymous,
    }
    result.update({key: profile.get(key, UNDEFINED) for key in (
        "firstName", "nextTier", "programType", "profileType", "isTravelAgent", "tierInfo"
    )})
    result.update({
        "promotionCode": _js_or(profile.get("promotionCode", UNDEFINED), None),
        "user": user,
        "loyaltyNumberR4u": _js_or(profile.get("loyaltyNumberR4u", UNDEFINED), None),
        "iataNumberR4u": _js_or(profile.get("iataNumberR4u", UNDEFINED), None),
    })
    return result


def customer_from_json(profile: dict[str, Any]) -> dict[str, Any]:
    """Customer.fromJSON: convert a caller-supplied raw profile to its DTO."""
    fields = {key: profile.get(key, UNDEFINED) for key in (
        "crowd", "rewardsNumber", "points", "tier", "firstName", "nextTier",
        "programType", "profileType", "isTravelAgent", "tierInfo"
    )}
    fields.update({
        "loyaltyNumberR4u": _nested_get(profile, "r4uData", "loyaltyNumberR4u"),
        "iataNumberR4u": _nested_get(profile, "travelAgent", "branch", "iata"),
        "promotionCode": _nested_get(profile, "companyData", "companyId"),
        "user": {
            "crowd": profile.get("crowd", UNDEFINED),
            "rewardsNumber": profile.get("rewardsNumber", UNDEFINED),
            "tier": profile.get("tier", UNDEFINED),
            "pcrLevel": _nested_get(profile, "r4uData", "pcrLevel"),
            "memberType": _nested_get(profile, "memberTypeExtended", "code"),
            "memberTypes": _js_or(profile.get("memberTypes", UNDEFINED), []),
        },
    })
    return customer_payload(fields)


def search_user_payload(profile: Any = None, lusso_ff: bool = False) -> Any:
    """pR's user projection is separate from the legacy Customer DTO."""
    if not js_truthy(profile):
        return None
    result = {"rewardsNumber": _nested_get(profile, "rewardsNumber")}
    crowd = _nested_get(profile, "crowd")
    pcr_level = _nested_get(profile, "r4uData", "pcrLevel")
    if js_truthy(crowd):
        result["crowd"] = crowd
    if js_truthy(pcr_level):
        result["pcrLevel"] = pcr_level
    result["memberType"] = _nested_get(profile, "memberTypeExtended", "code")
    member_types = _nested_get(profile, "memberTypes")
    if lusso_ff and js_truthy(member_types):
        result["memberTypes"] = member_types
    return result


def normalize_autocomplete(text: str) -> str:
    # Qs(): collapse runs before removing '&' and replacing '/'. Do not repeat.
    result = re.sub(r"[-.,·^\\()|]", " ", text)
    result = re.sub(r" +(?= )", "", result)
    return result.replace("&", "").replace("/", " ").strip(_JS_WHITESPACE).lower()


def occupancies_from_model(rooms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """pR's UI model: each numeric child age becomes {age: age}."""
    return [{"adults": room.get("adults", UNDEFINED),
             "children": [{"age": age} for age in _js_or(room.get("children", UNDEFINED), [])]}
            for room in rooms]


def occupancies_from_url(adults: list[Any], aoc: Any = UNDEFINED) -> list[dict[str, Any]]:
    """Legacy getStays: parseInt adults and per-room comma-separated child ages."""
    result = []
    for index, adult in enumerate(adults):
        age_value = aoc[index] if isinstance(aoc, (list, tuple)) and index < len(aoc) else UNDEFINED
        ages = _js_or(age_value, "")
        if not isinstance(ages, str):
            raise TypeError("Legacy aoc entries must be strings, as in the parsed page URL")
        pieces = ages.split(",")
        children = [] if pieces[0] in ("", "NaN") else [{"age": js_parse_int(age)} for age in pieces]
        result.append({"adults": js_parse_int(adult), "children": children})
    return result


def make_occupancies(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Input adapter: UI occupancy, ready API occupancies, or legacy URL arrays."""
    if "occupancy" in config:
        return occupancies_from_model(config["occupancy"])
    if "occupancies" in config:
        # Already a public API DTO. Do not coerce values that the JS preserves.
        return [{"adults": room.get("adults", UNDEFINED),
                 "children": [dict(child) if isinstance(child, dict) else {"age": child}
                              for child in _js_or(room.get("children", UNDEFINED), [])]}
                for room in config["occupancies"]]
    if "url_adults" in config:
        adults = config["url_adults"]
        aoc = config.get("url_aoc", UNDEFINED)
        return occupancies_from_url(adults if isinstance(adults, list) else [adults],
                                    aoc if isinstance(aoc, list) or aoc is UNDEFINED else [aoc])
    return [{"adults": 2, "children": []}]


def _set_key(value: Any):
    if value is UNDEFINED:
        return ("undefined",)
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        return ("number", "NaN" if isinstance(value, float) and math.isnan(value) else value)
    if isinstance(value, str):
        return ("string", value)
    return ("object", id(value))


def room_ids(rooms: list[dict[str, Any]]) -> list[Any]:
    """getStays reduce + Set: tmsCode before id, first occurrence retained."""
    result, seen = [], set()
    for room in rooms:
        tms, identifier = room.get("tmsCode", UNDEFINED), room.get("id", UNDEFINED)
        for value in [tms, identifier] if js_truthy(tms) else [identifier]:
            key = _set_key(value)
            if key not in seen:
                seen.add(key)
                result.append(value)
    return result


def availability_batches(hotels: list[dict[str, Any]], dynamic_config: dict[str, Any] | None = None):
    """Te -> category flags -> repository.availability -> Eu chunks.

    Accept original category records or the already projected Te records.
    mp.fromJson defaults a raw category's falsy groupCode to 'rhg'; repository
    grouping is strict for an already projected Te record.
    """
    config = dynamic_config if dynamic_config is not None else {}
    keys = config.get("keys", config)
    parsed = js_parse_int(keys.get("booking.sr.hotels.avail.request.limit", UNDEFINED), 10)
    size = 25 if isinstance(parsed, float) and math.isnan(parsed) else parsed
    if size < 1:
        # Eu would recurse without progress for <=0; do not hang the caller.
        raise ValueError("Availability request limit must be positive")
    selected = {"rhg": [], "lhg": []}
    for hotel in hotels:
        metadata = hotel.get("categories", hotel)
        if js_truthy(metadata.get("external", UNDEFINED)):
            continue
        projected = ("categories" not in hotel and "memberOnly" in hotel
                     and "memberOnlyRate" not in hotel and "external" not in hotel)
        group = metadata.get("groupCode", UNDEFINED)
        if not projected:
            group = _js_or(group, "rhg")
        if not isinstance(group, str) or group not in selected:
            continue
        member = hotel.get("memberOnly", UNDEFINED) if projected else js_truthy(metadata.get("memberOnlyRate", UNDEFINED))
        selected[group].append({"code": hotel.get("code", UNDEFINED), "memberOnly": member})
    return [{"group": group, "hotels": values[index:index + size]}
            for group, values in selected.items() for index in range(0, len(values), size)]


def multi_hotel_stay_payload(*, check_in: Any, check_out: Any, occupancies: Any,
                             search_type: Any, exclude_member_rates: Any,
                             source_of_business: Any, hotels: Any,
                             promotion_code: Any = UNDEFINED,
                             tax_included_region: Any = UNDEFINED,
                             voucher_code: Any = UNDEFINED, user: Any = UNDEFINED,
                             tier_type: Any = UNDEFINED, rewards_number: Any = UNDEFINED,
                             pay_with_points_rates: Any = UNDEFINED,
                             group: str = "rhg") -> dict[str, Any]:
    """_b JSON body. LHG repository calls omit its final payWithPointsRates arg."""
    body = {"checkInDate": check_in, "checkOutDate": check_out,
            "occupancies": occupancies, "searchType": search_type}
    if tax_included_region is not UNDEFINED:
        body["taxIncludedRegion"] = tax_included_region
    body.update({"excludeMemberRates": exclude_member_rates,
                 "sourceOfBusiness": source_of_business, "hotels": hotels})
    for field, value in (("promotionCode", promotion_code), ("voucherCode", voucher_code),
                         ("user", user), ("tierType", tier_type), ("rewardsNumber", rewards_number)):
        if js_truthy(value):
            body[field] = value
    body["payWithPointsRates"] = pay_with_points_rates if group == "rhg" else UNDEFINED
    return omit_undefined(body)


def search_location_fields(place_id: Any = UNDEFINED, hotel_codes: Any = UNDEFINED,
                           external_group: Any = False) -> dict[str, Any]:
    """CS's first attempted query; caller can retry hotels after an empty place result."""
    if js_truthy(place_id):
        result = {"placeId": place_id}
    elif js_truthy(hotel_codes):
        result = {"hotelCode": _js_join(hotel_codes)}
    else:
        raise ValueError("No placeId or hotel codes provided")
    if js_truthy(external_group):
        result["externalGroup"] = "true"
    return result


def saver_enabled(stored_value: Any = None) -> bool:
    return _js_or(stored_value, "true") == "true"


def local_today() -> date:
    # _n() is local midnight in the browser's timezone; no UTC-day conversion.
    return datetime.now().astimezone().date()


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # Request adapters accept date-only ISO strings; they do not claim to
    # implement the runtime's implementation-dependent Date.parse extensions.
    return date.fromisoformat(value)


def validate_stay_dates(start: Any = UNDEFINED, end: Any = UNDEFINED, *,
                        draft_end: Any = UNDEFINED, today: date | None = None) -> dict[str, Any]:
    """$t.validate for date/ISO-date inputs: partial ranges stay partial."""
    today = today if today is not None else local_today()
    complete = js_truthy(start) and js_truthy(end)
    try:
        parsed_start = _date(start) if js_truthy(start) else UNDEFINED
        parsed_end = _date(end) if js_truthy(end) else UNDEFINED
        parsed_draft = _date(draft_end) if js_truthy(draft_end) else UNDEFINED
        invalid = complete and (parsed_start < today or parsed_end <= parsed_start
                                or abs((parsed_end - parsed_start).days) > 90
                                or parsed_start > today + timedelta(days=730)
                                or parsed_end > today + timedelta(days=730))
    except (TypeError, ValueError):
        if not complete:
            raise ValueError("A partial range must use a valid ISO date") from None
        invalid = True
    if invalid:
        parsed_start, parsed_end, parsed_draft = today, today + timedelta(days=1), UNDEFINED
    length = abs((parsed_end - parsed_start).days) if parsed_start is not UNDEFINED and parsed_end is not UNDEFINED else 0
    draft_length = abs((parsed_draft - parsed_start).days) if parsed_start is not UNDEFINED and parsed_draft is not UNDEFINED else 0
    return {"start": parsed_start.isoformat() if parsed_start is not UNDEFINED else UNDEFINED,
            "end": parsed_end.isoformat() if parsed_end is not UNDEFINED else UNDEFINED,
            "draftEnd": parsed_draft.isoformat() if parsed_draft is not UNDEFINED else UNDEFINED,
            "length": length, "draftLength": draft_length}


def normalize_dates(config: dict[str, Any], today: date | None = None) -> tuple[str, str]:
    """Complete-request adapter around $t.validate.

    Explicit complete dates use the source validator. Absent dates use its
    today/tomorrow default. A start plus 'nights' is an intentional config
    shorthand, analogous to the UI's startDate/numberNights branch.
    """
    today = today if today is not None else local_today()
    start, end = config.get("check_in", UNDEFINED), config.get("check_out", UNDEFINED)
    if not js_truthy(start):
        start = today
    if not js_truthy(end):
        try:
            end = _date(start) + timedelta(days=int(config.get("nights", 1)))
        except (TypeError, ValueError, OverflowError):
            return today.isoformat(), (today + timedelta(days=1)).isoformat()
    result = validate_stay_dates(start, end, today=today)
    return result["start"], result["end"]


def calendar_occupancy_fields(occupancies: list[dict[str, Any]]) -> dict[str, Any]:
    """pe() from normalized API occupancies; keep empty room positions in aoc."""
    result = {"adults": _js_join(room.get("adults", UNDEFINED) for room in occupancies),
              "rooms": len(occupancies)}
    counts = [len(_js_or(room.get("children", UNDEFINED), [])) for room in occupancies]
    if max(counts, default=0) > 0:
        result["children"] = ",".join(str(count) for count in counts)
        result["aoc"] = ",".join(_js_join((child.get("age", UNDEFINED) if isinstance(child, dict) else child
                                          for child in _js_or(room.get("children", UNDEFINED), [])), "-")
                                  for room in occupancies)
    return result


def calendar_horizon(start: date, today: date | None = None, requested_days: int = 62) -> int:
    # Calendar I(): Kn is absolute. Starts past lastAllowed cannot occur in the
    # UI, but retain the source result rather than returning a negative count.
    last_allowed = (today if today is not None else local_today()) + timedelta(days=730)
    return abs((last_allowed - start).days) if start + timedelta(days=requested_days) > last_allowed else requested_days


def room_stay_payload(search_params: dict[str, Any], rooms: list[dict[str, Any]], *,
                      check_in: Any, check_out: Any, occupancies: list[dict[str, Any]],
                      source_of_business: Any, customer: dict[str, Any] | None = None,
                      tax_included_region: Any = UNDEFINED, hotel_tier_discount: Any = False,
                      drop_customer_promotion: bool = False) -> dict[str, Any]:
    """getStays/getStaysByRooms body construction with an explicit Customer DTO.

    drop_customer_promotion is the already evaluated lC/customer-flow predicate;
    it is not guessed from anonymous request samples.
    """
    profile = customer_payload() if customer is None else customer
    request = dict(search_params)
    voucher = request.pop("voucher", UNDEFINED)
    for key in ("aoc", "adults", "children", "room"):
        request.pop(key, None)
    request.update({"checkInDate": check_in, "checkOutDate": check_out,
                    "occupancies": occupancies, "taxIncludedRegion": tax_included_region,
                    "roomIds": room_ids(rooms), "sourceOfBusiness": source_of_business,
                    "groupedStays": True, "voucherCode": voucher if js_truthy(voucher) else UNDEFINED})
    if js_truthy(profile.get("isLogged", UNDEFINED)) and js_truthy(hotel_tier_discount) and request.get("searchType") == "lowest" and js_truthy(profile.get("tier", UNDEFINED)):
        request["tierDiscount"] = {"tierType": profile["tier"]}
    body = dict(request, **profile)
    if not js_truthy(profile.get("promotionCode", UNDEFINED)) and js_truthy(request.get("promotionCode", UNDEFINED)):
        body["promotionCode"] = request["promotionCode"]
    if drop_customer_promotion and not js_truthy(request.get("promotionCode", UNDEFINED)):
        body.pop("promotionCode", None)
    return omit_undefined(body)
