"""Load + validate config.yaml and .env into a single typed Settings object.

Loading is lenient (placeholders are allowed) so dry-runs and unit tests work without
real credentials. The ``doctor`` command is what enforces that every value is real
before a live run.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field

from .logging import register_secret

# repo-root/src/adbot/settings.py -> parents[2] == repo root
REPO_ROOT = Path(os.environ.get("ADBOT_ROOT", Path(__file__).resolve().parents[2]))
DEFAULT_CONFIG = Path(os.environ.get("ADBOT_CONFIG", REPO_ROOT / "config" / "config.yaml"))
PLACEHOLDER_TOKENS = ("XXXXXXXX", "GDRIVE_FOLDER_ID", "your-landing-page.example")


# ── config.yaml models ───────────────────────────────────────────────────────
class LeadDestination(BaseModel):
    type: str = "WEBSITE"
    link_url: str = ""
    lead_form_id: Optional[str] = None


class Budget(BaseModel):
    level: str = "CAMPAIGN"
    daily_amount_myr: float = 250
    currency: str = "MYR"
    adset_min_spend_myr: float = 50

    @property
    def daily_amount_cents(self) -> int:
        return int(round(self.daily_amount_myr * 100))

    @property
    def adset_min_spend_cents(self) -> int:
        return int(round(self.adset_min_spend_myr * 100))


class Targeting(BaseModel):
    countries: List[str] = Field(default_factory=lambda: ["MY"])
    age_min: int = 25
    age_max: int = 65
    advantage_audience: int = 1
    locales: List[int] = Field(default_factory=list)  # Meta locale ids; 1004 = Chinese (All)

    def to_spec(self) -> dict:
        spec = {
            "geo_locations": {"countries": self.countries},
            "age_min": self.age_min,
            "age_max": self.age_max,
            "targeting_automation": {"advantage_audience": self.advantage_audience},
        }
        if self.locales:
            spec["locales"] = self.locales
        return spec


class BuildCfg(BaseModel):
    creatives_per_adset: int = 10
    activate_after_build: bool = True


class MetaCfg(BaseModel):
    ad_account_id: str = "act_XXXXXXXX"
    page_id: str = "XXXXXXXX"
    instagram_user_id: Optional[str] = None
    pixel_id: str = "XXXXXXXX"
    objective: str = "OUTCOME_LEADS"
    optimization_goal: str = "OFFSITE_CONVERSIONS"
    conversion_event: str = "LEAD"
    special_ad_categories: List[str] = Field(default_factory=list)
    lead_destination: LeadDestination = Field(default_factory=LeadDestination)
    conversion_domain: str = ""
    call_to_action: str = "SIGN_UP"
    url_tags: str = ""  # UTM query string appended to destination URLs (supports Meta macros)
    budget: Budget = Field(default_factory=Budget)
    targeting: Targeting = Field(default_factory=Targeting)
    build: BuildCfg = Field(default_factory=BuildCfg)

    @property
    def account_path(self) -> str:
        """Graph API path segment, always 'act_<digits>'."""
        bare = self.ad_account_id.replace("act_", "")
        return f"act_{bare}"

    @property
    def promoted_object(self) -> dict:
        return {"pixel_id": self.pixel_id, "custom_event_type": self.conversion_event}

    @property
    def conversion_domain_bare(self) -> str:
        """Meta wants a bare domain (no scheme/path) for website-conversion ads."""
        domain = re.sub(r"^https?://", "", (self.conversion_domain or "").strip())
        return domain.split("/")[0]


class Naming(BaseModel):
    prefix: str = "STOCKBLOOM"
    weekly_off_label: str = "ADBOT_WEEKLY_OFF"

    def campaign_name(self, suffix: str) -> str:
        return f"{self.prefix} | {suffix}"


class DriveCfg(BaseModel):
    creatives_folder_id: str = "GDRIVE_FOLDER_ID"
    carousel_subfolder_marker: str = "carousel"
    script_sidecar_ext: str = ".txt"


class GoogleDocsCfg(BaseModel):
    caption_log_doc_id: str = ""
    idea_backlog_doc_id: str = ""


class KpiCfg(BaseModel):
    cpl_threshold_myr: float = 40.0
    cpl_min_spend_myr: float = 80.0
    cpl_lookback: str = "last_3d"  # 'week_thu' = week-to-date from Thursday, or any Meta date_preset
    pause_zero_lead_after_spend: bool = True
    cpl_hold: List[str] = Field(default_factory=list)  # ad-name substrings temporarily exempt from auto-pause


class CpaCfg(BaseModel):
    """Cost per real paid acquisition, from the Paid Student List sheet (RM2,399/2,099 a pax)."""
    enabled: bool = False
    spreadsheet_id: str = ""
    sales_tab: str = "Paid Student List"
    price_myr: float = 2399.0
    target_myr: float = 720.0           # primary target CPA
    healthy_max_myr: float = 800.0      # end of healthy range (NOT an auto-pause line)
    max_acceptable_myr: float = 960.0   # above here -> pause candidate after diagnosis
    hard_stop_myr: float = 1200.0       # above here (with real sales) -> auto-pause
    conversion_days: int = 14           # don't judge CPA / 'no sales' until this old
    min_spend_myr: float = 1000.0       # need at least this much spend to fairly judge CPA


# ── secrets (.env / environment) ─────────────────────────────────────────────
class Secrets(BaseModel):
    meta_token: str = ""
    meta_app_secret: str = ""
    google_sa_json: str = ""
    anthropic_api_key: str = ""


# ── top-level ────────────────────────────────────────────────────────────────
class Settings(BaseModel):
    meta: MetaCfg = Field(default_factory=MetaCfg)
    naming: Naming = Field(default_factory=Naming)
    drive: DriveCfg = Field(default_factory=DriveCfg)
    google_docs: GoogleDocsCfg = Field(default_factory=GoogleDocsCfg)
    kpi: KpiCfg = Field(default_factory=KpiCfg)
    cpa: CpaCfg = Field(default_factory=CpaCfg)
    schedule: dict = Field(default_factory=dict)
    secrets: Secrets = Field(default_factory=Secrets)
    config_path: str = str(DEFAULT_CONFIG)


def load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, '#' comments. Does not overwrite existing env."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _resolve_sa_json() -> str:
    """Service-account key from either env var; pick whichever actually holds a key.

    Accepts base64 OR raw JSON in either field, and skips a junk value in one field when the
    other holds a usable key (e.g. a stale/wrong _B64 sitting next to a correct _JSON).
    """
    def _decode(value: str) -> str:
        if value.lstrip().startswith("{"):
            return value  # raw JSON pasted into the _B64 field
        try:
            return base64.b64decode(value).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return value  # not base64 — hand to build_credentials (may be a path)

    candidates = []
    b64 = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_B64", "").strip()
    if b64:
        candidates.append(_decode(b64))
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        candidates.append(raw)
    for cand in candidates:
        if cand.lstrip().startswith("{") and "private_key" in cand:
            return cand  # a real key beats junk in the other field
    return candidates[0] if candidates else ""


def _load_secrets() -> Secrets:
    secrets = Secrets(
        meta_token=os.environ.get("META_SYSTEM_USER_TOKEN", ""),
        meta_app_secret=os.environ.get("META_APP_SECRET", ""),
        google_sa_json=_resolve_sa_json(),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
    )
    register_secret(secrets.meta_token)
    register_secret(secrets.meta_app_secret)
    register_secret(secrets.anthropic_api_key)
    return secrets


def load_settings(config_path: Optional[Path] = None) -> Settings:
    """Read config.yaml + .env into a validated Settings object."""
    load_dotenv(REPO_ROOT / ".env")
    path = Path(config_path or DEFAULT_CONFIG)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = data or {}
    settings = Settings(
        meta=MetaCfg(**(data.get("meta") or {})),
        naming=Naming(**(data.get("naming") or {})),
        drive=DriveCfg(**(data.get("drive") or {})),
        google_docs=GoogleDocsCfg(**(data.get("google_docs") or {})),
        kpi=KpiCfg(**(data.get("kpi") or {})),
        cpa=CpaCfg(**(data.get("cpa") or {})),
        schedule=data.get("schedule") or {},
        secrets=_load_secrets(),
        config_path=str(path),
    )
    return settings


def has_placeholder(value: str) -> bool:
    """True if a config string still holds a template placeholder."""
    return any(tok in (value or "") for tok in PLACEHOLDER_TOKENS)
