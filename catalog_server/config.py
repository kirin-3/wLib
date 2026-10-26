# SPDX-License-Identifier: GPL-3.0-or-later
import os
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
