from _common.client import RequestSpec, path_value
from _common import parameters as p


def normalize_text(text):
    return p.normalize_autocomplete(text)


def build_request(config):
    text = normalize_text(config.get("text", ""))
    if not text:
        raise ValueError("Search text is empty after frontend normalization")
    return RequestSpec("GET", "/zimba-api/autocomplete/" + path_value(text),
                       required_response_fields=("places", "hotels"))
