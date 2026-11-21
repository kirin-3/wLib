# SPDX-License-Identifier: GPL-3.0-or-later
import os
from urllib.parse import urlsplit
from dataclasses import dataclass, field

REPOSITORY_URL = "https://github.com/kirin-3/wLib"
USER_AGENT = f"wLib-catalog-builder/1 (+{REPOSITORY_URL})"

DEFAULT_DATA_DIR = "/var/lib/wlib-catalog"
SNAPSHOTS_TO_KEEP = 4
# xz preset 6 needs about 94 MB of compressor memory; 9 would swap the e2-micro.
XZ_PRESET = 6


@dataclass(frozen=True)
class SiteLimits:
    min_delay: float
    budget: int | None = None
    window: float = 0.0
    # Wait out a full budget instead of ending the source's run; for short windows.
    wait: bool = False


# One entry per pacer key. F95's anonymous limit is about 100 per hour.
SITE_LIMITS: dict[str, SiteLimits] = {
    "f95": SiteLimits(min_delay=3.0, budget=90, window=3600.0),
    # Thread pages, outside the feed's limit. About 80 threads change per day.
    "f95_threads": SiteLimits(min_delay=5.0, budget=90, window=3600.0),
    # LewdCorner's login-gated feed, and its guest thread pages on their own budget.
    "lc": SiteLimits(min_delay=3.0, budget=90, window=3600.0),
    "lc_threads": SiteLimits(min_delay=5.0, budget=60, window=3600.0),
    # The guest listings of the Plus forums, 20 threads per page (about 35 pages).
    "lc_plus": SiteLimits(min_delay=5.0, budget=40, window=3600.0),
    "dlsite": SiteLimits(min_delay=3.0),
    "ryuugames": SiteLimits(min_delay=3.0),
    "vndb": SiteLimits(min_delay=1.0, budget=150, window=300.0, wait=True),
}


@dataclass(frozen=True)
class R2Config:
    account_id: str
    # Kept out of repr so a logged config never shows the token.
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    bucket: str = "wlib-db"

    @property
    def endpoint(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


def proxy_env_name(site: str) -> str:
    return f"WLIB_CATALOG_{site.upper()}_PROXY"


def site_proxies_from_env() -> dict[str, str]:
    """Per-site proxies, e.g. WLIB_CATALOG_DLSITE_PROXY=http://127.0.0.1:8888.

    Only plain http:// proxy URLs without credentials are accepted; HTTPS
    requests reach the site through them with CONNECT.
    """
    proxies: dict[str, str] = {}
    for site in SITE_LIMITS:
        name = proxy_env_name(site)
        value = os.environ.get(name, "").strip()
        if not value:
            continue
        parts = urlsplit(value)
        if (
            parts.scheme != "http"
            or not parts.hostname
            or parts.username is not None
            or parts.path not in ("", "/")
        ):
            raise ValueError(f"{name} must look like http://host:port")
        proxies[site] = value
    return proxies


BROWSER_SITES_ENV = "WLIB_CATALOG_BROWSER_SITES"
F95_COOKIE_ENV = "WLIB_CATALOG_F95_COOKIE"
LC_COOKIE_ENV = "WLIB_CATALOG_LC_COOKIE"


def browser_sites_from_env() -> frozenset[str]:
    """Sites fetched through the headless browser, e.g. WLIB_CATALOG_BROWSER_SITES=f95,dlsite."""
    value = os.environ.get(BROWSER_SITES_ENV, "")
    sites = frozenset(part.strip() for part in value.split(",") if part.strip())
    unknown = sorted(sites - SITE_LIMITS.keys())
    if unknown:
        raise ValueError(
            f"{BROWSER_SITES_ENV} has unknown site(s): {', '.join(unknown)}; "
            + f"choose from {', '.join(SITE_LIMITS)}"
        )
    if "vndb" in sites:
        # VNDB is a JSON API that needs POST requests; the browser only sends GET.
        raise ValueError(f"{BROWSER_SITES_ENV} cannot include vndb")
    return sites


def _xf_user_cookie_from_env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    value = value.removeprefix("xf_user=")
    # The browser shows the cookie as `<user id>%2C<key>`; a copy that decoded
    # the comma still works.
    value = value.replace(",", "%2C")
    if any(char in value for char in ' ;"\\') or not value.isprintable():
        # The message never shows the value: it is a login secret.
        raise ValueError(f"{name} must be only the value of the xf_user cookie")
    return value


def f95_cookie_from_env() -> str | None:
    """The value of F95's `xf_user` login cookie, from /etc/wlib-catalog/f95.env."""
    return _xf_user_cookie_from_env(F95_COOKIE_ENV)


def lc_cookie_from_env() -> str | None:
    """The value of LewdCorner's `xf_user` login cookie, from /etc/wlib-catalog/lc.env."""
    return _xf_user_cookie_from_env(LC_COOKIE_ENV)


DISCORD_WEBHOOK_ENV = "WLIB_CATALOG_DISCORD_WEBHOOK"
_DISCORD_HOSTS = frozenset(
    {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}
)


def discord_webhook_from_env() -> str | None:
    """The Discord webhook for run reports, from /etc/wlib-catalog/discord.env."""
    value = os.environ.get(DISCORD_WEBHOOK_ENV, "").strip()
    if not value:
        return None
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or parts.hostname not in _DISCORD_HOSTS
        or not parts.path.startswith("/api/webhooks/")
    ):
        # The message never shows the value: the URL is a secret.
        raise ValueError(
            f"{DISCORD_WEBHOOK_ENV} must be a https://discord.com/api/webhooks/ URL"
        )
    return value


def data_dir() -> str:
    return os.environ.get("WLIB_CATALOG_DATA_DIR") or DEFAULT_DATA_DIR


def r2_config_from_env() -> R2Config | None:
    """Credentials come from /etc/wlib-catalog/r2.env via systemd's EnvironmentFile."""
    values = {
        name: os.environ.get(name, "").strip()
        for name in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
    }
    if not all(values.values()):
        return None
    return R2Config(
        account_id=values["R2_ACCOUNT_ID"],
        access_key_id=values["R2_ACCESS_KEY_ID"],
        secret_access_key=values["R2_SECRET_ACCESS_KEY"],
        bucket=os.environ.get("R2_BUCKET", "").strip() or "wlib-db",
    )
