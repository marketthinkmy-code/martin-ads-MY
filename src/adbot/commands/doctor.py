"""Preflight: validate credentials, config, and connectivity before any live run."""

from __future__ import annotations

from typing import Callable, List, Tuple

from . import drive_client, graph_client
from ..captions import AudienceNotReady, load_audience
from ..logging import final_summary, get_logger
from ..settings import Settings, has_placeholder


def run(settings: Settings, *, dry_run: bool = False) -> int:
    log = get_logger()
    results: List[Tuple[str, bool, str]] = []

    def check(name: str, fn: Callable[[], str]) -> None:
        try:
            detail = fn()
            results.append((name, True, detail))
        except Exception as exc:  # noqa: BLE001 - doctor must never crash
            results.append((name, False, str(exc)))

    # ── secrets ────────────────────────────────────────────────────────────
    def _secrets() -> str:
        missing = [n for n, v in [
            ("META_SYSTEM_USER_TOKEN", settings.secrets.meta_token),
            ("GOOGLE_SERVICE_ACCOUNT_JSON", settings.secrets.google_sa_json),
            ("ANTHROPIC_API_KEY", settings.secrets.anthropic_api_key),
        ] if not v]
        if missing:
            raise RuntimeError("missing: " + ", ".join(missing))
        return "all required secrets present"
    check("secrets present", _secrets)

    # ── config placeholders ─────────────────────────────────────────────────
    def _config() -> str:
        fields = {
            "meta.ad_account_id": settings.meta.ad_account_id,
            "meta.page_id": settings.meta.page_id,
            "meta.pixel_id": settings.meta.pixel_id,
            "lead_destination.link_url": settings.meta.lead_destination.link_url,
            "meta.conversion_domain": settings.meta.conversion_domain,
            "drive.creatives_folder_id": settings.drive.creatives_folder_id,
        }
        bad = [k for k, v in fields.items() if has_placeholder(v)]
        if bad:
            raise RuntimeError("still placeholder: " + ", ".join(bad))
        return "config.yaml placeholders filled"
    check("config filled", _config)

    # ── audience framework ───────────────────────────────────────────────────
    def _audience() -> str:
        load_audience(settings)
        return "config/audience.md ready (no TODO markers)"
    check("audience framework", _audience)

    # ── Meta connectivity ────────────────────────────────────────────────────
    def _meta() -> str:
        graph = graph_client(settings)
        me = graph.me()
        acct = graph.get_object(settings.meta.account_path, "name,currency,account_status")
        page = graph.get_object(settings.meta.page_id, "name")
        graph.get_object(settings.meta.pixel_id, "name")
        return (f"token ok (user {me.get('name')}); account '{acct.get('name')}' "
                f"{acct.get('currency')}; page '{page.get('name')}'; pixel ok")
    check("meta api", _meta)

    # ── Drive connectivity ───────────────────────────────────────────────────
    def _drive() -> str:
        drive = drive_client(settings)
        children = drive.list_children(settings.drive.creatives_folder_id)
        return f"folder readable ({len(children)} item(s) at top level)"
    check("google drive", _drive)

    # ── Docs (optional ids) ──────────────────────────────────────────────────
    def _docs() -> str:
        ids = [settings.google_docs.caption_log_doc_id, settings.google_docs.idea_backlog_doc_id]
        if not any(ids):
            return "no Doc ids set — both will be created on first write"
        from . import docs_client
        docs = docs_client(settings)
        for doc_id in [i for i in ids if i]:
            docs.read_text(doc_id)
        return "configured Doc(s) readable by the service account"
    check("google docs", _docs)

    # ── report ───────────────────────────────────────────────────────────────
    ok_count = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        log.info("  [%s] %-20s %s", "PASS" if ok else "FAIL", name, detail)
    all_ok = ok_count == len(results)
    final_summary(log, f"doctor: {ok_count}/{len(results)} checks passed"
                       + ("" if all_ok else " — fix FAIL items before a live run"))
    return 0 if all_ok else 1
