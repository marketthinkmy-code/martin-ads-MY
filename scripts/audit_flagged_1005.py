"""ADHD copy check v2 (read-only, 5 Oct).

v1's page-post read needs pages_read_engagement, which the system user token
lacks. The ad CREATIVE's own body/object_story_spec is ad-account data and
readable with ads permissions, so: scan the HK and SG accounts' ads for
creatives built on the milk/bread posts and search every text field of the
creative (body, title, object_story_spec, asset_feed_spec) for ADHD wording.
"""
from __future__ import annotations

import json
import re

from adbot.commands import graph_client
from adbot.logging import final_summary, get_logger
from adbot.settings import load_settings

ACCOUNTS = [("HK", "act_1179668409969241"), ("SG老", "act_1024930575770087")]
P = "341825319024143_"
MILK_POSTS = {
    P + "122140406480485585": "准备早餐面包",
    P + "122140405604485585": "我不会买牛奶",
    P + "122184069494485585": "牛奶+面包",
    P + "122195326100485585": "麵包當早餐 (Hook 2)",
}
ADHD_PAT = re.compile(r"adhd|过动|過動|多动|多動|注意力|专注|專注", re.IGNORECASE)


def main() -> None:
    log = get_logger()
    g = graph_client(load_settings())
    seen, flagged = {}, []
    for label, acct in ACCOUNTS:
        for a in g._get_all(f"{acct}/ads",
                            {"fields": "id,name,"
                                       "creative{id,body,title,effective_object_story_id,"
                                       "object_story_spec,asset_feed_spec}",
                             "limit": 250}):
            cr = a.get("creative") or {}
            sid = cr.get("effective_object_story_id")
            if sid not in MILK_POSTS or sid in seen:
                continue
            text = json.dumps(cr, ensure_ascii=False)
            hits = sorted(set(x.lower() for x in ADHD_PAT.findall(text)))
            body = cr.get("body") or (((cr.get("object_story_spec") or {})
                                       .get("video_data") or {}).get("message")) or ""
            seen[sid] = True
            name = MILK_POSTS[sid]
            if hits:
                flagged.append(name)
                idx = ADHD_PAT.search(text).start()
                log.info("⚠️ %-18s 命中 %s → …%s…", name, hits,
                         text[max(0, idx - 70):idx + 90].replace("\\n", " "))
            else:
                log.info("✅ %-18s creative 全部文字字段无 ADHD/过动/注意力/专注 字眼"
                         "（正文 %d 字，creative %s，来源 %s %s）",
                         name, len(body), cr.get("id"), label, a.get("id"))
    missing = [v for k, v in MILK_POSTS.items() if k not in seen]
    final_summary(log, f"ADHD check: 覆盖 {len(seen)}/4 帖 · 命中 {flagged or '无'}"
                       f"{' · 未找到 creative: ' + ', '.join(missing) if missing else ''}")


if __name__ == "__main__":
    main()
