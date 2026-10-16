from _common.client import RequestSpec
from _common import parameters as p


def build_request(config):
    codes = config.get("hotel_codes")
    code = ",".join(codes) if codes else config.get("hotel_code")
    if not isinstance(code, str) or not code:
        raise ValueError("Provide hotel_codes or hotel_code from search/autocomplete")
    params = {"code": code}
    if p.js_truthy(config.get("external_group", False)):
        params["externalGroup"] = "true"
    return RequestSpec("GET", "/zimba-api/search-result/hotels", params=params,
                       headers={"X-Origin": "SR"}, required_response_fields=("hotels",))
