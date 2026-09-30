"""Фоновые задачи Web Push.

Отправка уведомления — это сетевой запрос к push-службе, иногда к нескольким
подпискам сразу. Раньше общая рассылка выполнялась прямо в запросе админки, а
личное уведомление — фоновой задачей внутри веб-процесса. Теперь и то и другое
идёт через общую очередь: запрос не ждёт сеть, а недоставленное можно повторить.
"""

import logging

from celery import shared_task

from users.services.push import (
    send_admin_push,
    send_direct_message_push,
    send_moderator_report_push,
)


logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def deliver_direct_message_push(
    self,
    *,
    recipient_id: int,
    room_slug: str,
    sender_id: int | None = None,
):
    """Доставляет нейтральное уведомление о личном сообщении."""
    return send_direct_message_push(
        recipient_id=recipient_id,
        room_slug=room_slug,
        sender_id=sender_id,
    )


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def deliver_moderator_report_push(self):
    """Сообщает модераторам о новой жалобе."""
    return send_moderator_report_push().delivered


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def deliver_admin_push(
    self,
    *,
    subscription_ids: list[int],
    title: str,
    body: str,
    url: str,
):
    """Рассылает объявление по уже выбранным подпискам."""
    from users.models import PushSubscription

    subscriptions = PushSubscription.objects.filter(
        id__in=subscription_ids,
        enabled=True,
    )
    result = send_admin_push(
        subscriptions=subscriptions,
        title=title,
        body=body,
        url=url,
    )
    logger.info(
        "admin_push_delivered delivered=%d failed=%d removed=%d",
        result.delivered,
        result.failed,
        result.removed,
    )
    return result.delivered
