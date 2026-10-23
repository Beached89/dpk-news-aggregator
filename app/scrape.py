"""CLI entry point for the scraper.

    python -m app.scrape --once            # one pass, then exit
    python -m app.scrape --loop            # run forever on a schedule
    python -m app.scrape --once --limit 10 --langs kp
"""
from __future__ import annotations

import argparse
import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from . import config
from .scraper import run_once


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    p = argparse.ArgumentParser(description="KCNA scraper")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run a single pass and exit")
    mode.add_argument("--loop", action="store_true", help="run on a repeating schedule")
    p.add_argument("--langs", default=",".join(config.LANGS), help="comma list, e.g. kp or kp,en")
    p.add_argument("--limit", type=int, default=config.MAX_ARTICLES_PER_RUN)
    p.add_argument("--interval", type=int, default=config.SCRAPE_INTERVAL_MIN,
                   help="minutes between runs in --loop mode")
    p.add_argument("--source", action="append",
                   choices=["kcna", "rodong", "vok", "magazine", "agriculture",
                            "38north", "dailynk"],
                   help="scrape only this source (repeatable); default = SCRAPE_* config flags")
    p.add_argument("--maintenance", dest="maintenance", action="store_true", default=None,
                   help="run cross-source upkeep (prune/juche/dedup) — exactly one container should")
    p.add_argument("--no-maintenance", dest="maintenance", action="store_false",
                   help="skip cross-source upkeep (for the secondary scraper containers)")
    args = p.parse_args()

    _setup_logging()
    langs = [s.strip() for s in args.langs.split(",") if s.strip()]
    sources = set(args.source) if args.source else None
    maintenance = True if args.maintenance is None else args.maintenance

    def _pass():
        run_once(langs, args.limit, sources, maintenance)

    if args.loop:
        # Run immediately, then on the interval.
        _pass()
        sched = BlockingScheduler(timezone="UTC")
        sched.add_job(_pass, "interval", minutes=args.interval, max_instances=1, coalesce=True)
        logging.getLogger("kcna").info("scheduled every %d min; Ctrl-C to stop", args.interval)
        try:
            sched.start()
        except (KeyboardInterrupt, SystemExit):
            pass
    else:
        _pass()


if __name__ == "__main__":
    main()
