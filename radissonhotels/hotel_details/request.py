from _common.client import RequestSpec, path_value


def build_request(config):
    code = path_value(config.get("hotel_code"))
    return RequestSpec("GET", f"/content-api/hotels/{code}", style="legacy",
                       transaction=False, required_response_fields=("hotel",))
