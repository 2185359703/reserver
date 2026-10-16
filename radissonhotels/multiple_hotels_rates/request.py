from _common.client import RequestSpec
from _common.context import currency_headers, source, taxes
from _common import parameters as p


def build_request(config):
    """One RequestSpec per RHG/LHG batch, matching repository.availability."""
    if "category_hotels" not in config:
        raise ValueError("Load public category_hotels first; memberOnly/groupCode cannot be invented")
    # Te maps selected codes in their search-result order, not catalog order.
    selected = config.get("hotel_codes", [])
    by_code = {}
    for hotel in config["category_hotels"]:
        by_code.setdefault(hotel.get("code"), hotel)
    hotels = [by_code[code] for code in selected if code in by_code] if selected else list(config["category_hotels"])
    raw_categories = [{"code": hotel["code"], "categories": hotel} for hotel in hotels]
    batches = p.availability_batches(raw_categories, config.get("dynamic_config"))
    if not batches:
        raise ValueError("No internal RHG/LHG hotels selected after category filtering")
    check_in, check_out = p.normalize_dates(config)
    profile = config.get("profile")
    user = p.search_user_payload(profile, config.get("lusso_ff", False))
    excluded = p.exclude_member_rates(profile, config.get("loyalty_consent"), config.get("model_exclude_member_rates", False))
    specs = []
    for batch in batches:
        body = p.multi_hotel_stay_payload(
            check_in=check_in, check_out=check_out, occupancies=p.make_occupancies(config),
            search_type=config.get("search_type", "lowest"), exclude_member_rates=excluded,
            source_of_business=source(config), hotels=batch["hotels"],
            promotion_code=config.get("promotion_code", p.UNDEFINED),
            tax_included_region=taxes(config), voucher_code=config.get("voucher_code", p.UNDEFINED),
            user=user, tier_type=(profile or {}).get("tier", p.UNDEFINED),
            rewards_number=(profile or {}).get("rewardsNumber", p.UNDEFINED),
            pay_with_points_rates=config.get("pay_with_points_rates", False), group=batch["group"])
        specs.append(RequestSpec("POST", "/availability-api/multiple-hotels-rates", body=body,
                                 headers={"X-Version": "V2", **currency_headers(config)},
                                 required_response_fields=("hotels",)))
    return specs


def fetch(client, config):
    resolved = client.resolve_context(config, categories=True, modern_currency=True)
    return {"batches": [client.send(spec) for spec in build_request(resolved)]}
