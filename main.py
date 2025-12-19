from core import config
from playwright.sync_api import sync_playwright

url = config.CONFIG["base_url"]
raw_cookies = config.CONFIG["cookies"]

def _normalize_cookie(c):
    name = c.get("name")
    value = c.get("value")
    if not isinstance(name, str) or not isinstance(value, str) or not name or not value:
        return None
    domain = c.get("domain") or "chat.deepseek.com"
    path = c.get("path") or "/"
    r = {
        "name": name,
        "value": value,
        "domain": domain,
        "path": path,
        "httpOnly": bool(c.get("httpOnly", False)),
        "secure": bool(c.get("secure", False)),
    }
    ss = c.get("sameSite")
    if isinstance(ss, str):
        m = {"lax": "Lax", "strict": "Strict", "none": "None"}
        r["sameSite"] = m.get(ss.lower())
    expires = c.get("expires") or c.get("expirationDate")
    if expires and not c.get("session", False):
        try:
            r["expires"] = int(expires)
        except Exception:
            pass
    return r

normalized_cookies = []
allowed_cookies = {"smidV2","ds_session_id"}
for c in (raw_cookies or []):
    nc = _normalize_cookie(c)
    if isinstance(nc, dict) and nc.get("name") in allowed_cookies and nc.get("value"):
        normalized_cookies.append(nc)

try:
    from core.uagen import UA as UA_OVERRIDE
except Exception:
    UA_OVERRIDE = None

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(url)
    ua = UA_OVERRIDE or config.CONFIG.get("ua")
    context = browser.contexts[0] if browser.contexts else (
        browser.new_context(user_agent=ua) if ua else browser.new_context()
    )
    if normalized_cookies:
        context.add_cookies(normalized_cookies)
    page = context.new_page()
    page.goto("https://chat.deepseek.com/", wait_until="domcontentloaded")