import os
import json
from dotenv import load_dotenv

load_dotenv()

_cookies_path = os.getenv("cookies")
_cookies = []
if _cookies_path and os.path.exists(_cookies_path):
    with open(_cookies_path, "r") as f:
        _cookies = json.load(f)

CONFIG = {
    "base_url": os.getenv("base_url"),
    "browserless_url": os.getenv("browserless_url", "ws://localhost:3000"),
    "cookies": _cookies,
    "ua": os.getenv("ua"),
    "user": os.getenv("user"),
    "password": os.getenv("password"),
    "max_concurrency": int(os.getenv("MAX_CONCURRENCY", "5")),
}
