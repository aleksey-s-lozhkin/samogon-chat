from celery import shared_task
from celery.signals import worker_ready
from django.db import transaction
from django.utils import timezone

from chat.models import BartenderJob
from chat.services.bartender import BartenderUnavailable, bartender
from chat.services.events import broadcast_message
from chat.services.messages import MessageService
from users.services.push import send_direct_message_push


@worker_ready.connect
def restore_queued_bartender_jobs(**kwargs):
    """Возвращает в Redis задания, сохранённые во время недоступности брокера."""
    for job_id in BartenderJob.objects.filter(
        status=BartenderJob.Status.QUEUED,
    ).values_list("id", flat=True).iterator():
        process_bartender_job.delay(str(job_id))


@shared_task(bind=True, max_retries=2, default_retry_delay=10)
def process_bartender_job(self, job_id):
    job = BartenderJob.objects.select_related("room", "user", "question").get(pk=job_id)
    if job.status == BartenderJob.Status.SUCCEEDED:
        return
    job.status = BartenderJob.Status.STARTED
    job.started_at = timezone.now()
    job.error_code = ""
    job.save(update_fields=("status", "started_at", "error_code"))
    try:
        reply = bartender.reply(
            room_name=job.room.name,
            username=job.user.username,
            text=job.question.text,
        ).text
    except BartenderUnavailable as error:
        if self.request.retries < self.max_retries:
            job.status = BartenderJob.Status.QUEUED
            job.error_code = "bartender_unavailable"
            job.save(update_fields=("status", "error_code"))
            raise self.retry(exc=error)
        job.status = BartenderJob.Status.FAILED
        job.error_code = "bartender_unavailable"
        job.finished_at = timezone.now()
        job.save(update_fields=("status", "error_code", "finished_at"))
        return
    except Exception:
        job.status = BartenderJob.Status.FAILED
        job.error_code = "internal_error"
        job.finished_at = timezone.now()
        job.save(update_fields=("status", "error_code", "finished_at"))
        raise

    with transaction.atomic():
        job = BartenderJob.objects.select_for_update().select_related("room", "user").get(pk=job_id)
        if job.status == BartenderJob.Status.SUCCEEDED:
            return
        response = MessageService.create_message(
            user_id=bartender.get_bartender_user().id,
            room=job.room,
            text=reply,
            recipient_id=job.user_id if job.private else None,
        )
        job.response = response
        job.status = BartenderJob.Status.SUCCEEDED
        job.finished_at = timezone.now()
        job.error_code = ""
        job.save(update_fields=("response", "status", "finished_at", "error_code"))
    broadcast_message(response)
    if job.private:
        send_direct_message_push(recipient_id=job.user_id, room_slug=job.room.slug)
