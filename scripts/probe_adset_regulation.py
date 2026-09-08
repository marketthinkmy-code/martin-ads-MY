"""Read-only probe: which regional-regulation declaration does Meta want on a NEW MY ad set?

Since 2026-09-08 every new ad set in the MY account fails with
  [400] Provide a verified advertiser so that ads in this ad set can be delivered to audiences
        in the selected locations.
while every ad set created before that date keeps delivering. SG's ad sets carry
  regional_regulated_categories = ["SINGAPORE_UNIVERSAL"]
  regional_regulation_identities = {singapore_universal_beneficiary, singapore_universal_payer}
so the MY equivalent is presumably the same pair of fields with Malaysia's category / keys —
but the docs are unreachable from here and the names must not be guessed into a real build.

So this asks Meta directly, WITHOUT creating anything: every POST carries
  execution_options = ["validate_only"]
which makes the Graph API run the full validation and return the errors instead of the object.
An unknown enum value makes Meta answer with the list of accepted values, which is exactly the
question. Nothing is written; the identity object and the account are only read.

Spec JSON (ADBOT_PROBE_SPEC, default scripts/probe_specs/my_regulation.json):
  campaign_id       existing PAUSED campaign to validate the ad set against
  source_adset_id   ad set whose targeting the probe copies (same as the real build would)
  also_exclude[]    extra excluded audiences (same as the real build)
  read_objects[]    {label, id, fields} — objects to GET and print first (identity / account)
  candidates[]      {label, fields} — extra ad-set fields to validate, one POST each
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from adbot.commands import graph_client
from adbot.settings import REPO_ROOT, load_settings

_KEEP = ("geo_locations", "age_min", "age_max", "genders", "locales",
         "excluded_custom_audiences", "flexible_spec")


def _err(exc: Exception) -> str:
    payload = getattr(exc, "payload", None)
    if isinstance(payload, dict):
        return json.dumps(payload.get("error", payload), ensure_ascii=False)
    return str(exc)


def main() -> None:
    spec_path = Path(os.environ.get("ADBOT_PROBE_SPEC", "scripts/probe_specs/my_regulation.json"))
    if not spec_path.is_absolute():
        spec_path = REPO_ROOT / spec_path
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    settings = load_settings()
    m = settings.meta
    graph = graph_client(settings)
    account = m.account_path

    print("===== READS =====")
    for item in spec.get("read_objects") or []:
        try:
            obj = graph.get_object(str(item["id"]), item.get("fields", "id,name"))
            print(f"{item.get('label', item['id'])}\n    {json.dumps(obj, ensure_ascii=False)}")
        except Exception as exc:  # noqa: BLE001 - one unreadable object must not stop the probe
            print(f"{item.get('label', item['id'])}\n    !! {_err(exc)}")

    t = (graph.get_object(str(spec["source_adset_id"]), "targeting") or {}).get("targeting", {}) or {}
    targeting = {k: t[k] for k in _KEEP if k in t}
    aa = (t.get("targeting_automation") or {}).get("advantage_audience")
    targeting["targeting_automation"] = {"advantage_audience": 0 if aa == 0 else 1}
    for extra in spec.get("also_exclude", []):
        excl = targeting.setdefault("excluded_custom_audiences", [])
        if not any(str(e.get("id")) == str(extra) for e in excl):
            excl.append({"id": str(extra)})

    base = dict(
        campaign_id=str(spec["campaign_id"]), name="PROBE validate_only (never created)",
        optimization_goal=m.optimization_goal, billing_event="IMPRESSIONS",
        promoted_object=m.promoted_object, targeting=targeting, status="PAUSED",
        daily_budget=5000, bid_strategy="LOWEST_COST_WITHOUT_CAP",
        execution_options=["validate_only"],
    )

    print("\n===== VALIDATE-ONLY PROBES (nothing is created) =====")
    for cand in spec.get("candidates") or []:
        fields = dict(base)
        fields.update(cand.get("fields") or {})
        fields["execution_options"] = ["validate_only"]   # belt and braces — never drop this
        label = cand.get("label", "?")
        try:
            out = graph.create_adset(account, **fields)
            print(f"[{label}]\n    extra  {json.dumps(cand.get('fields') or {}, ensure_ascii=False)}"
                  f"\n    OK     {json.dumps(out, ensure_ascii=False)}")
        except Exception as exc:  # noqa: BLE001 - the error text IS the result
            print(f"[{label}]\n    extra  {json.dumps(cand.get('fields') or {}, ensure_ascii=False)}"
                  f"\n    ERROR  {_err(exc)}")
    print("\nDONE")


if __name__ == "__main__":
    main()
