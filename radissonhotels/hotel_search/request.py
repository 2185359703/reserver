from _common.client import ApiError, RequestSpec
from _common import parameters as p


def build_request(config):
    params = p.search_location_fields(config.get("place_id", p.UNDEFINED),
                                      config.get("hotel_codes", p.UNDEFINED),
                                      config.get("external_group", False))
    return RequestSpec("GET", "/zimba-api/search-result/hotels/search", params=params, required_response_fields=("hotels",))


def fetch(client, config):
    try:
        return client.send(build_request(config))
    except ApiError:
        # CS tries hotel codes if its place lookup produced no data.
        if p.js_truthy(config.get("place_id")) and p.js_truthy(config.get("hotel_codes")):
            return client.send(build_request(dict(config, place_id=None)))
        raise
