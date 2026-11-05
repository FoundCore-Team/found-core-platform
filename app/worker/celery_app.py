from celery import Celery

from app.core.config import settings

EMBED_PROFILE_TASK = "embeddings.embed_profile"
SWEEP_STALE_EMBEDDINGS_TASK = "embeddings.sweep_stale"

celery_app = Celery("found_core", broker=settings.worker.broker_url)
celery_app.conf.update(
    # Loaded by the worker and beat only: the web app sends tasks by name and never imports them.
    imports=("app.worker.tasks",),
    task_default_queue=settings.embedding.queue_name,
    # A task is acknowledged after it finishes, so a worker that dies mid-task does not lose it.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    task_time_limit=settings.embedding.task_time_limit_seconds,
    broker_connection_timeout=settings.embedding.enqueue_timeout_seconds,
    broker_connection_retry_on_startup=True,
    # Only the connect timeout: a socket timeout shorter than the worker's blocking read would break its polling.
    broker_transport_options={"socket_connect_timeout": settings.embedding.enqueue_timeout_seconds},
    beat_schedule={
        "sweep-stale-embeddings": {
            "task": SWEEP_STALE_EMBEDDINGS_TASK,
            "schedule": float(settings.embedding.sweep_interval_seconds),
        },
    },
)
