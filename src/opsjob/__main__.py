"""`python -m opsjob` — Container Apps Job 진입점."""

import os
import sys

from evals.transport import emit
from opsjob.run import config_from_env, run
from opsmetrics.prices import load_prices


def main() -> int:
    from azure.identity import ManagedIdentityCredential

    from opsjob.azure import CosmosRuns, QueueSender

    cfg = config_from_env(os.environ)
    credential = ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID"))
    summary = run(
        cfg,
        queue=QueueSender(os.environ["QUEUE_ENDPOINT"], credential),
        store=CosmosRuns(os.environ["COSMOS_ENDPOINT"], os.environ["COSMOS_DATABASE"], credential),
        prices=load_prices(),
    )
    emit(cfg.run_id, [cfg.out_dir / "docs.json", cfg.out_dir / "summary.json"], print)
    return 0 if summary.get("mode") == "collect" or summary.get("submitted") else 1


if __name__ == "__main__":
    sys.exit(main())
