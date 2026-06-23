"""Thin Meta Marketing (Graph) API client — the only place that talks to Meta.

Does what the Meta MCP cannot: upload videos/images, build static carousels, and manage
ad labels (used for the stateless weekly OFF/ON cycle). Everything destructive funnels
through here so retries, auth, and error handling live in one place.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential)

API_VERSION = "v21.0"
BASE = f"https://graph.facebook.com/{API_VERSION}"


class GraphError(RuntimeError):
    """A Meta Graph API error with the structured payload attached."""

    def __init__(self, status: int, payload: Dict[str, Any]):
        self.status = status
        self.payload = payload
        err = (payload or {}).get("error", {})
        msg = err.get("error_user_msg") or err.get("message") or str(payload)
        super().__init__(f"[{status}] {msg}")


class _Retryable(Exception):
    """Internal marker for transient failures worth retrying."""


class GraphClient:
    def __init__(self, token: str, app_secret: str = "", *, session: Optional[requests.Session] = None,
                 timeout: int = 60):
        self.token = token
        self.app_secret = app_secret
        self.timeout = timeout
        self.session = session or requests.Session()

    # ── low-level ────────────────────────────────────────────────────────────
    def _auth_params(self) -> Dict[str, str]:
        params = {"access_token": self.token}
        if self.app_secret:
            params["appsecret_proof"] = hmac.new(
                self.app_secret.encode(), self.token.encode(), hashlib.sha256
            ).hexdigest()
        return params

    @retry(reraise=True, stop=stop_after_attempt(4),
           wait=wait_exponential(multiplier=2, min=2, max=20),
           retry=retry_if_exception_type(_Retryable))
    def _request(self, method: str, path: str, *, params: Optional[Dict] = None,
                 data: Optional[Dict] = None, files: Optional[Dict] = None) -> Dict[str, Any]:
        url = f"{BASE}/{path.lstrip('/')}"
        call_params = self._auth_params()
        if params:
            call_params.update(params)
        try:
            resp = self.session.request(method, url, params=call_params, data=data,
                                        files=files, timeout=self.timeout)
        except requests.RequestException as exc:  # network blip
            raise _Retryable(str(exc)) from exc

        if resp.status_code in (429, 500, 502, 503, 504):
            raise _Retryable(f"HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            payload = resp.json()
        except ValueError:
            payload = {"raw": resp.text}
        if resp.status_code >= 400:
            raise GraphError(resp.status_code, payload)
        return payload

    def _get_all(self, path: str, params: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """GET an edge and follow cursor pagination, returning all ``data`` rows."""
        out: List[Dict[str, Any]] = []
        page = self._request("GET", path, params=params)
        while True:
            out.extend(page.get("data", []))
            nxt = (page.get("paging") or {}).get("next")
            if not nxt:
                return out
            # follow the absolute next URL directly (already signed query params present)
            resp = self.session.get(nxt, timeout=self.timeout)
            page = resp.json()

    # ── identity / preflight reads ─────────────────────────────────────────────
    def me(self) -> Dict[str, Any]:
        return self._request("GET", "me", params={"fields": "id,name"})

    def get_object(self, object_id: str, fields: str) -> Dict[str, Any]:
        return self._request("GET", object_id, params={"fields": fields})

    # ── media upload (impossible via MCP) ──────────────────────────────────────
    def upload_image(self, account_path: str, file_path: str) -> str:
        """Upload an image; return its image_hash."""
        with open(file_path, "rb") as fh:
            payload = self._request("POST", f"{account_path}/adimages",
                                    files={"filename": fh})
        images = payload.get("images", {})
        first = next(iter(images.values()))
        return first["hash"]

    def upload_video(self, account_path: str, file_path: str, name: str,
                     *, poll_seconds: int = 5, poll_timeout: int = 900) -> str:
        """Upload a video via Meta's resumable chunked protocol; return the id once ready.

        The one-shot ``source`` upload fails for large files, so we always use the
        start -> transfer(chunks) -> finish phases, which Meta sizes via the offsets.
        """
        file_size = os.path.getsize(file_path)
        start = self._request("POST", f"{account_path}/advideos",
                              data={"upload_phase": "start", "file_size": file_size})
        session_id = start["upload_session_id"]
        video_id = start["video_id"]
        start_offset, end_offset = int(start["start_offset"]), int(start["end_offset"])

        with open(file_path, "rb") as fh:
            while start_offset < end_offset:
                fh.seek(start_offset)
                chunk = fh.read(end_offset - start_offset)
                resp = self._request(
                    "POST", f"{account_path}/advideos",
                    data={"upload_phase": "transfer", "upload_session_id": session_id,
                          "start_offset": start_offset},
                    files={"video_file_chunk": ("chunk", chunk, "application/octet-stream")},
                )
                start_offset, end_offset = int(resp["start_offset"]), int(resp["end_offset"])

        self._request("POST", f"{account_path}/advideos",
                      data={"upload_phase": "finish", "upload_session_id": session_id, "name": name})
        self._wait_video_ready(video_id, poll_seconds, poll_timeout)
        return video_id

    def _wait_video_ready(self, video_id: str, poll_seconds: int, poll_timeout: int) -> None:
        deadline = time.time() + poll_timeout
        while time.time() < deadline:
            status = self.get_object(video_id, "status").get("status", {})
            state = status.get("video_status")
            if state == "ready":
                return
            if state == "error":
                raise GraphError(400, {"error": {"message": f"video {video_id} processing failed"}})
            time.sleep(poll_seconds)
        raise GraphError(408, {"error": {"message": f"video {video_id} not ready after {poll_timeout}s"}})

    def get_video_thumbnail(self, video_id: str) -> Optional[str]:
        """Return a Meta-generated thumbnail URL for a processed video (preferred if present)."""
        thumbs = self._get_all(f"{video_id}/thumbnails", {"fields": "uri,is_preferred"})
        if not thumbs:
            return None
        preferred = next((t for t in thumbs if t.get("is_preferred")), thumbs[0])
        return preferred.get("uri")

    # ── entity creation ────────────────────────────────────────────────────────
    def create_campaign(self, account_path: str, **fields) -> Dict[str, Any]:
        return self._request("POST", f"{account_path}/campaigns", data=_encode(fields))

    def create_adset(self, account_path: str, **fields) -> Dict[str, Any]:
        return self._request("POST", f"{account_path}/adsets", data=_encode(fields))

    def create_adcreative(self, account_path: str, **fields) -> Dict[str, Any]:
        return self._request("POST", f"{account_path}/adcreatives", data=_encode(fields))

    def create_ad(self, account_path: str, **fields) -> Dict[str, Any]:
        return self._request("POST", f"{account_path}/ads", data=_encode(fields))

    def update_status(self, entity_id: str, status: str) -> Dict[str, Any]:
        """status is ACTIVE or PAUSED."""
        return self._request("POST", entity_id, data={"status": status})

    # ── reads for monitoring / scoping ─────────────────────────────────────────
    def list_campaigns(self, account_path: str) -> List[Dict[str, Any]]:
        """Every campaign in the account — whole-account scope for the monitor + weekly OFF."""
        return self._get_all(f"{account_path}/campaigns",
                             {"fields": "id,name,effective_status", "limit": 200})

    def find_campaigns_by_prefix(self, account_path: str, prefix: str) -> List[Dict[str, Any]]:
        return [c for c in self.list_campaigns(account_path)
                if (c.get("name") or "").startswith(prefix)]

    def list_ads_under_campaign(self, campaign_id: str) -> List[Dict[str, Any]]:
        # adset{promoted_object} rides along so the monitor can tell which conversion event
        # each ad is optimized for (and never judge a non-registration ad on registration CPL).
        return self._get_all(f"{campaign_id}/ads",
                            {"fields": "id,name,effective_status,created_time,adset_id,adset{promoted_object}",
                             "limit": 200})

    def get_ad_insight(self, ad_id: str, date_preset: Optional[str] = None,
                       time_range: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
        params: Dict[str, Any] = {"fields": "spend,actions,cost_per_action_type"}
        if time_range:
            params["time_range"] = json.dumps(time_range)
        else:
            params["date_preset"] = date_preset or "last_3d"
        rows = self._request("GET", f"{ad_id}/insights", params=params).get("data", [])
        return rows[0] if rows else None

    def account_insights(self, account_path: str, *, level: str, fields: str,
                         date_preset: Optional[str] = None,
                         time_range: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
        """One insights row per entity at ``level`` (campaign/adset/ad) for the whole account.

        A single call returns every campaign's (or ad's) spend for a window — far cheaper
        than per-entity calls. Pass date_preset (e.g. last_30d, maximum) OR a time_range
        {since, until} for windows Meta has no preset for (e.g. 60 days).
        """
        params: Dict[str, Any] = {"level": level, "fields": fields, "limit": 500}
        if time_range:
            params["time_range"] = json.dumps(time_range)
        else:
            params["date_preset"] = date_preset or "maximum"
        return self._get_all(f"{account_path}/insights", params)

    # ── ad labels (stateless weekly OFF/ON coordination) ───────────────────────
    def get_or_create_label(self, account_path: str, name: str) -> str:
        rows = self._get_all(f"{account_path}/adlabels", {"fields": "id,name", "limit": 200})
        for row in rows:
            if row.get("name") == name:
                return row["id"]
        return self._request("POST", f"{account_path}/adlabels", data={"name": name})["id"]

    def set_ad_labels(self, ad_id: str, label_ids: List[str]) -> None:
        """Replace an ad's labels (we exclusively own our ads, so replace is safe)."""
        self._request("POST", ad_id,
                      data={"adlabels": json.dumps([{"id": lid} for lid in label_ids])})

    def list_ad_ids_by_label(self, label_id: str) -> List[Dict[str, Any]]:
        return self._get_all(f"{label_id}/ads",
                            {"fields": "id,name,effective_status,adset_id,campaign_id", "limit": 200})


def _encode(fields: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-encode dict/list values; the Graph API expects them as JSON strings in form data."""
    out: Dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        out[key] = json.dumps(value) if isinstance(value, (dict, list)) else value
    return out
