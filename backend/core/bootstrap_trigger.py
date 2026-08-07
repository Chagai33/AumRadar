# -*- coding: utf-8 -*-
"""Trigger Bootstrap (Stage 2) as a Cloud Run Job in production, or as an
in-process BackgroundTask in local dev — the exact pattern job_trigger.py uses for
the scan.

Hundreds of playlist-track reads blow past the ~26s Netlify proxy limit, so the
API never runs the pull inline: it launches the `aumradar-bootstrap` Cloud Run Job
(which runs backend.bootstrap_job) and the browser just polls status. In local dev,
where no Job exists, it falls back to a BackgroundTask so the app still works
end-to-end on a laptop (there the run dies if the container recycles — that's fine
for dev, and a resume picks up from the checkpoint).
"""
import os
import time
import logging

from .storage_manager import storage
from .bootstrap import bootstrap, STATE_FILE

logger = logging.getLogger(__name__)


def _write_starting_state():
    """Reflect the run in shared state the instant it's triggered, so the UI shows
    it immediately instead of looking idle through the Job's ~1-2 min cold start.
    is_running keeps the frontend polling; status='starting' is exempted by the
    Job's concurrency guard, and get_status()'s 120s staleness clears it if the Job
    never boots."""
    storage.save_json(STATE_FILE, {
        "is_running": True, "status": "starting", "phase": "loading",
        "pulled": 0, "total": 0, "songs": 0, "current": "Starting bootstrap…",
        "logs": [], "heartbeat": time.time(),
    })


def _job_configured() -> bool:
    """True when the Service knows which Cloud Run Job to launch (set in prod)."""
    return bool(os.getenv("BOOTSTRAP_JOB_NAME") and os.getenv("GOOGLE_CLOUD_PROJECT"))


def _run_cloud_job(mode: str):
    """Launch the Cloud Run Job, overriding BOOTSTRAP_MODE for this one execution.
    The google-cloud-run client is imported lazily so local dev is unaffected."""
    from google.cloud import run_v2  # lazy: only needed on the cloud path

    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    region = os.getenv("BOOTSTRAP_JOB_REGION", os.getenv("SCAN_JOB_REGION", "europe-west1"))
    job = os.environ["BOOTSTRAP_JOB_NAME"]

    client = run_v2.JobsClient()
    name = f"projects/{project}/locations/{region}/jobs/{job}"
    overrides = run_v2.RunJobRequest.Overrides(
        container_overrides=[
            run_v2.RunJobRequest.Overrides.ContainerOverride(
                env=[run_v2.EnvVar(name="BOOTSTRAP_MODE", value=mode)]
            )
        ]
    )
    client.run_job(request=run_v2.RunJobRequest(name=name, overrides=overrides))


def trigger_bootstrap_job(mode: str, sp=None, background_tasks=None):
    """Start Bootstrap. mode: 'run' (fresh) | 'resume' (from checkpoint).

    Cloud mode launches the Job (browser-independent); local mode runs the same
    pipeline as a BackgroundTask (sp is the caller's user client, used only there)."""
    _write_starting_state()

    if _job_configured():
        _run_cloud_job(mode)
        return {"status": "job_triggered", "mode": mode}

    # A single one of the two vars set is almost certainly a PROD misconfig
    # (Cloud Run does NOT auto-inject GOOGLE_CLOUD_PROJECT) — warn loudly so a run
    # silently going inline instead of as a Job is visible in the logs.
    if os.getenv("BOOTSTRAP_JOB_NAME") or os.getenv("GOOGLE_CLOUD_PROJECT"):
        logger.warning(
            "BOOTSTRAP_JOB_NAME/GOOGLE_CLOUD_PROJECT not BOTH set (BOOTSTRAP_JOB_NAME "
            "set=%s, GOOGLE_CLOUD_PROJECT set=%s) — running Bootstrap INLINE as a "
            "BackgroundTask, NOT as a Cloud Run Job. It will die if the container is "
            "recycled (resume from the checkpoint).",
            bool(os.getenv("BOOTSTRAP_JOB_NAME")), bool(os.getenv("GOOGLE_CLOUD_PROJECT")),
        )

    if background_tasks is None:
        return {"status": "error", "message": "no Cloud Run Job configured and no task runner available"}
    background_tasks.add_task(bootstrap.run_bootstrap, sp, mode == "resume")
    return {"status": "started_local", "mode": mode}
