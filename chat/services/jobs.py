import logging

from django.db import transaction

from chat.models import BartenderJob
from chat.tasks import process_bartender_job


logger = logging.getLogger(__name__)


def dispatch_bartender_job(job_id):
    try:
        process_bartender_job.delay(str(job_id))
    except Exception:
        # Задание остаётся в PostgreSQL и будет поднято при старте worker.
        logger.warning("bartender_job_dispatch_failed job_id=%s", job_id)


def enqueue_bartender_job(*, user, room, question, private):
    job = BartenderJob.objects.create(
        user=user,
        room=room,
        question=question,
        private=private,
    )
    transaction.on_commit(lambda: dispatch_bartender_job(job.id))
    return job
