import json
import logging
from dataclasses import dataclass

from django.conf import settings
from django.urls import reverse
from django.utils.crypto import salted_hmac
from pywebpush import WebPushException, webpush

from users.models import PushSubscription


logger = logging.getLogger(__name__)


def device_id_for_subscription(subscription: PushSubscription) -> str:
    """Возвращает стабильный непрозрачный ID без endpoint и push-ключей."""
    return salted_hmac(
        "samogon.push-device",
        str(subscription.pk),
    ).hexdigest()[:32]


@dataclass(frozen=True)
class PushDeliveryResult:
    delivered: int = 0
    failed: int = 0
    removed: int = 0


def send_push_payload(*, subscriptions, payload: dict) -> PushDeliveryResult:
    """Отправляет payload выбранным подпискам без раскрытия endpoint в журнале."""
    if not settings.WEB_PUSH_ENABLED:
        return PushDeliveryResult()

    delivered = failed = removed = 0
    data = json.dumps(payload, ensure_ascii=False)
    for subscription in subscriptions.iterator():
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {
                        "p256dh": subscription.p256dh,
                        "auth": subscription.auth,
                    },
                },
                data=data,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.VAPID_SUBJECT},
                timeout=5,
            )
        except WebPushException as exc:
            status_code = getattr(exc.response, "status_code", None)
            if status_code in {404, 410}:
                subscription.delete()
                removed += 1
            else:
                logger.warning("Web Push delivery failed with status %s", status_code)
                failed += 1
        except Exception:
            # Текст исключения может содержать endpoint, поэтому не журналируем его.
            logger.exception("Unexpected Web Push delivery failure", exc_info=False)
            failed += 1
        else:
            delivered += 1
    return PushDeliveryResult(delivered=delivered, failed=failed, removed=removed)


def send_direct_message_push(*, recipient_id: int, room_slug: str) -> int:
    """Доставляет нейтральное уведомление и удаляет мёртвые endpoint."""
    if not settings.WEB_PUSH_ENABLED:
        return 0

    payload = {
        "title": "Новое личное сообщение",
        "body": "В Самогоне ждёт личная реплика.",
        "url": reverse("chat:chat", args=[room_slug]),
        "tag": f"direct-message-{room_slug}",
    }
    subscriptions = PushSubscription.objects.filter(
        user_id=recipient_id,
        enabled=True,
        direct_messages_enabled=True,
    )
    return send_push_payload(
        subscriptions=subscriptions,
        payload=payload,
    ).delivered


def send_admin_push(
    *, subscriptions, title: str, body: str, url: str
) -> PushDeliveryResult:
    """Отправляет подтверждённое администратором объявление выбранной аудитории."""
    return send_push_payload(
        subscriptions=subscriptions,
        payload={
            "title": title,
            "body": body,
            "url": url,
            "tag": "admin-announcement",
        },
    )


def send_push_self_test(*, subscriptions, url: str) -> PushDeliveryResult:
    """Отправляет нейтральную диагностическую проверку выбранному устройству."""
    return send_push_payload(
        subscriptions=subscriptions,
        payload={
            "title": "Проверка уведомлений",
            "body": "Web Push в Самогоне работает.",
            "url": url,
            "tag": "push-self-test",
        },
    )
