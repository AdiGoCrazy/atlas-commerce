"""Local polling worker for Atlas regional recommendation cycles."""

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from adaptive_commerce.application.dashboard import bootstrap_database
from adaptive_commerce.application.worker import run_all_active_regions, scheduler_loop
from adaptive_commerce.integrations.observability import initialize_tracing


def main() -> None:
    """Run one worker pass or start the local polling scheduler."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(os.environ.get(
        "ATLAS_DB_PATH", "data/atlas_commerce.db")))
    parser.add_argument("--once", action="store_true", help="run one regional pass and exit")
    parser.add_argument("--interval", type=int, default=300, help="poll interval in seconds")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    status = initialize_tracing()
    logging.getLogger(__name__).info("neatlogs_status=%s detail=%s", status.status, status.detail)
    bootstrap_database(args.db)
    if args.once:
        for result in run_all_active_regions(args.db):
            logging.getLogger(__name__).info(
                "worker_region_result region=%s cycle=%s status=%s",
                result.cycle.region_id, result.cycle.id, result.cycle.status.value,
            )
        return
    scheduler_loop(args.db, args.interval)


if __name__ == "__main__":
    main()
