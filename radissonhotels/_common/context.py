"""Adapters from declared UI state and public responses to parameter inputs."""
from _common import parameters as p


def http_headers(config):
    """Explicit HTTP context observed on the accepted anonymous browser request."""
    major = str(config.get("browser_major", 155))
    platform = config.get("platform", "Windows")
    user_agent = config.get("user_agent") or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    )
    return {
        "User-Agent": user_agent,
        "sec-ch-ua": f'"Google Chrome";v="{major}", "Chromium";v="{major}", "Not(A:Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": f'"{platform}"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Priority": "u=1, i",
    }


def source(config):
    return p.source_of_business(config.get("viewport_width", 1280))


def currency_headers(config, *, legacy=False):
    value = p.currency(config.get("preferred_currency", p.UNDEFINED), config.get("currency_response"), legacy=legacy)
    return {"X-Currency": value} if p.js_truthy(value) else {}


def taxes(config, *, calendar=False):
    calc = p.calendar_tax_region if calendar else p.tax_region
    return calc(config.get("device_country"), config.get("dynamic_config", {}))
