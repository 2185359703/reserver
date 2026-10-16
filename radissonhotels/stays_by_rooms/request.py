from _common.client import RequestSpec, path_value
from _common.context import currency_headers, source, taxes
from _common import parameters as p


def build_request(config):
    hotel_code = config.get("hotel_code")
    path_value(hotel_code)
    rooms = config.get("rooms")
    if rooms is None and config.get("room_ids"):
        rooms = [{"id": identifier} for identifier in config["room_ids"]]
    if not rooms:
        raise ValueError("Load rooms from the public room API first")
    check_in, check_out = p.normalize_dates(config)
    # Preserve getStays' URL-derived search-object order. JSON.stringify keeps
    # these positions when the normalized values and Customer DTO are merged.
    search = {"hotelCode": hotel_code, "checkInDate": check_in, "checkOutDate": check_out,
              "searchType": config.get("search_type", "lowest"),
              "promotionCode": config.get("promotion_code")}
    search.update(config.get("search_params", {}))
    if "promotion_code" in config:
        search["promotionCode"] = config["promotion_code"]
    if "voucher_code" in config:
        search["voucher"] = config["voucher_code"]
    customer = (p.customer_from_json(config["profile"]) if config.get("profile") is not None
                else p.customer_payload(config.get("customer")))
    body = p.room_stay_payload(
        search, rooms, check_in=check_in, check_out=check_out,
        occupancies=p.make_occupancies(config), source_of_business=source(config),
        customer=customer, tax_included_region=taxes(config),
        hotel_tier_discount=config.get("hotel_tier_discount", False),
        drop_customer_promotion=config.get("drop_customer_promotion", False))
    headers = currency_headers(config, legacy=True)
    if p.saver_enabled(config.get("saver_storage_value")):
        headers["Saver"] = "true"
    return RequestSpec("POST", "/availability-api/stays-by-rooms", body=body, headers=headers,
                       style="legacy", required_response_fields=("rooms",))


def fetch(client, config):
    resolved = client.resolve_context(config, rooms=True)
    profile = config.get("profile")
    customer = p.customer_from_json(profile) if profile is not None else p.customer_payload(config.get("customer"))
    if p.js_truthy(customer.get("isLogged")) and "hotel_tier_discount" not in config:
        from hotel_details.request import build_request as hotel_request
        hotel = client.send(hotel_request(config))
        resolved["hotel_tier_discount"] = hotel["hotel"].get("tierDiscount", False)
    return client.send(build_request(resolved))
