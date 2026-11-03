import logging

from celery import shared_task
from celery.signals import worker_ready
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from chat.models import AtmosphereLine, BartenderJob
from chat.services.atmosphere import AtmosphereUnavailable, generate_atmosphere_line
from chat.services.bartender import BartenderUnavailable, bartender
from chat.services.bartender_context import build_bartender_conversation
from chat.services.events import broadcast_message
from chat.services.messages import MessageService
from users.services.push import send_direct_message_push


logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=15)
def generate_atmosphere_line_candidate(self):
    """Создаёт черновик; публикация остаётся ручным решением в админке."""
    try:
        text = generate_atmosphere_line()
    except AtmosphereUnavailable as error:
        raise self.retry(exc=error)
    line, _ = AtmosphereLine.objects.get_or_create(
        text=text,
        defaults={
            "kind": AtmosphereLine.Kind.SEMEN,
            "status": AtmosphereLine.Status.DRAFT,
            "generated_by_model": settings.OLLAMA_MODEL,
        },
    )
    return line.pk


@shared_task(bind=True)
def bird_tick(self):
    """Один тик птицы: решает, пора ли говорить, и планирует следующий.

    Расписание живёт внутри задачи, а не в Celery beat: beat — это ещё один
    контейнер и ещё одно место, где настраивается время. Тик сам назначает
    следующий и переподнимается при старте воркера, поэтому перезапуск ничего
    не ломает.

    Дубли безопасны: решение принимается под блокировкой строки беседы, а
    интервал проверяется дважды — второй раз уже под ней.
    """
    from chat.services.bird import BirdConfigUnavailable, load_config, speak

    try:
        config = load_config()
    except BirdConfigUnavailable:
        # Состояние неизвестно: не выключаемся навсегда, пробуем снова.
        logger.error("bird_config_unavailable, повторю позже", exc_info=True)
        schedule_bird_tick()
        return

    if not config.enabled:
        # Осознанно выключена — цепочка останавливается. Включение из админки
        # ставит первый тик само.
        return

    try:
        speak(config=config)
    except Exception:
        # Птица не должна ронять воркер. Даже упав, она пробует снова.
        logger.warning("bird_tick_failed", exc_info=True)

    try:
        schedule_bird_tick()
    except Exception:
        # Оборванная цепочка — тихая поломка: пишем так, чтобы было видно.
        logger.warning("bird_reschedule_failed", exc_info=True)


def schedule_bird_tick() -> bool:
    """Назначает следующий тик, не спрашивая настроек.

    Включённость проверяет сам тик. Так сделано после 3 ноября: при старте
    воркера база не разрешалась по имени, проверка настроек падала, и цепочка
    не поднималась вовсе — птица замолчала, а выглядело это как «выключена».
    Пустой тик раз в пять минут дешевле, чем потерянная птица.
    """
    bird_tick.apply_async(countdown=settings.BIRD_CHECK_SECONDS)
    return True


@worker_ready.connect
def restore_queued_bartender_jobs(**kwargs):
    """Возвращает в Redis задания, сохранённые во время недоступности брокера."""
    # Птицу запускаем первым делом и без условий: если база в этот момент
    # недоступна, восстановление заданий упадёт, но цепочка птицы поднимется.
    # Ошибку глушим — обработчик сигнала не должен мешать воркеру подняться.
    try:
        schedule_bird_tick()
    except Exception:
        logger.warning("bird_schedule_failed", exc_info=True)

    try:
        for job_id in BartenderJob.objects.filter(
            status=BartenderJob.Status.QUEUED,
        ).values_list("id", flat=True).iterator():
            process_bartender_job.delay(str(job_id))
    except Exception:
        logger.warning("bartender_restore_failed", exc_info=True)


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
        conversation = build_bartender_conversation(
            room=job.room,
            question=job.question,
            user_id=job.user_id,
            private=job.private,
        )
        reply = bartender.reply(
            text=job.question.text,
            context=conversation.history,
            current_author=conversation.current_author,
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
            reply_to_id=job.question_id,
        )
        job.response = response
        job.status = BartenderJob.Status.SUCCEEDED
        job.finished_at = timezone.now()
        job.error_code = ""
        job.save(update_fields=("response", "status", "finished_at", "error_code"))
    broadcast_message(response)
    if job.private:
        send_direct_message_push(recipient_id=job.user_id, room_slug=job.room.slug)
