"""Apply post-build fixes to the live (paused) Campaign #1, and patch the caption manifest.

Fixes: (1) 🔴 prefix on every headline, (2) ad-set Languages = Chinese (All) [locale 1004],
(3) ad names = Notion/script titles. Creatives are immutable, so each ad gets a freshly
built creative (with the 🔴 headline) swapped in. Idempotent enough to re-run.
"""

from __future__ import annotations

import json
from pathlib import Path

from adbot import build_1_1_10, state
from adbot.clients.graph import GraphClient
from adbot.creative_groups import VIDEO, Asset, Unit
from adbot.settings import REPO_ROOT, load_settings

NAMES = {
    "video_1": "Video 1：最痛苦的是什么",
    "video_2": "Video 2：采访的角度",
    "video_3": "Video 3：今天看了几久的盘？",
    "video_4": "Video 4：90% 的老手都中毒了",
    "video_5": "Video 5：小白怎么可能学得会",
    "video_6": "Video 6：很多人讲，你的【1分钟交易策略】根本就是诈骗！",
    "video_7": "Video 7：用 Moomoo 买股票",
    "video_8": "Video 8：你的钱放 FD，是在等死",
    "video_9": "Video 9：你不是不会赚，是赚了又还回去",
    "video_10": "Video 10：盯盘 8 小时，其实是在折磨自己",
}
DRIVE_IDS = {
    "video_1": "11RqwHJqqzmUo52fEMCvRmBwhT_u3MmAj",
    "video_2": "1JweZZrHCf8So_7dXxcTw2ZktOkaZ_404",
    "video_3": "1IE22yyTyzpl8eHShjqoSDZBh6M8C8Ptw",
    "video_4": "17snXLY-kPMivoM6d4sc4D60QemP_YPKJ",
    "video_5": "1TOnXPSKWvDAvF7FmosNWJTp6xv8reDlK",
    "video_6": "1x3mCw1v_5mGSzj2OOHVtvwa41df1dC6w",
    "video_7": "1KgtxWHZ5GRzpVMCk7dZcAx21gTAOL1aT",
    "video_8": "1lckVuXIFEPRoDJYZELmMfy9dbJSGIfXE",
    "video_9": "1nRCho0kV6Hsf2QdB640ghD2b_Drkv23B",
    "video_10": "1k95BMFRhphKvQoFtKQmZr54kn2_SoaTV",
}
ORDER = [f"video_{i}" for i in range(1, 11)]


def patch_manifest() -> dict:
    path = Path(REPO_ROOT) / "config" / "captions_june2026.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for cid, entry in manifest.items():
        if not entry["headline"].startswith("🔴"):
            entry["headline"] = "🔴 " + entry["headline"]
        if cid in NAMES:
            entry["name"] = NAMES[cid]
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    manifest = patch_manifest()
    s = load_settings()  # picks up targeting.locales = [1004]
    g = GraphClient(s.secrets.meta_token, s.secrets.meta_app_secret, timeout=120)
    media_cache = state.load("media_cache")
    ents = state.load("entities")
    adset_id, ad_ids = ents["adset_id"], ents["ad_ids"]

    # (2) Languages = Chinese (All) — resend the full targeting spec with locales.
    before = g.get_object(adset_id, "targeting,name").get("targeting", {})
    print("targeting.locales BEFORE:", before.get("locales"))
    g._request("POST", adset_id, data={"targeting": json.dumps(s.meta.targeting.to_spec())})
    after = g.get_object(adset_id, "targeting").get("targeting", {})
    print("targeting.locales AFTER :", after.get("locales"))

    # Confirm customer-lifecycle (blank/None => "Get conversions from all audiences", the default).
    print("ad-set customer-acquisition field:",
          g.get_object(adset_id, "is_incremental_attribution_enabled").get(
              "is_incremental_attribution_enabled", "not set (=all audiences)"))

    # (1)+(3) rebuild each creative with 🔴 headline and rename the ad.
    for cid, ad_id in zip(ORDER, ad_ids):
        vid = media_cache[DRIVE_IDS[cid]]["meta_id"]
        unit = Unit(cid, VIDEO, [Asset(file_id=DRIVE_IDS[cid], name=f"{cid}.mp4",
                                       mime="video/mp4", meta_id=vid)])
        thumb = g.get_video_thumbnail(vid)
        spec = build_1_1_10.creative_spec(s, unit, manifest[cid], thumbnail_url=thumb)
        new_cr = g.create_adcreative(s.meta.account_path, **spec)["id"]
        g._request("POST", ad_id, data={"creative": json.dumps({"creative_id": new_cr}),
                                        "name": NAMES[cid]})
        print(f"  {cid}: ad {ad_id} -> creative {new_cr}, name '{NAMES[cid]}', headline '{manifest[cid]['headline']}'")

    print("DONE")


if __name__ == "__main__":
    main()
