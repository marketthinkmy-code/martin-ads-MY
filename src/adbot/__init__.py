"""adbot — Meta ads automation for the STOCK BLOOM trading course.

Subcommands (see ``python -m adbot --help``):
    doctor       preflight: validate credentials, account, page, pixel, Drive/Docs
    sync         download Drive creatives, upload to Meta, group into 10 units
    build        create the 1-1-10 structure (campaign / ad set / 10 ads) + caption log
    monitor      pause ads whose CPL exceeds the threshold
    weekly_off   pause ALL managed ads (Wed 15:00 GMT+8 kill switch)
    weekly_on    resume exactly the ads weekly_off paused (Thu 00:00 GMT+8)
    intel        read live creatives -> micro-segment angles/hooks/ideas -> Google Doc
"""

__version__ = "0.1.0"
