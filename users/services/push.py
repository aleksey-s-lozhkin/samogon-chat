import ipaddress
import json
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from django.utils.crypto import salted_hmac
from pywebpush import WebPushException, webpush

from users.models import PushSubscription, User


logger = logging.getLogger(__name__)

# Имена, которые заведомо указывают на локальную сеть или служебную зону.
FORBIDDEN_ENDPOINT_HOSTS = {"localhost"}
FORBIDDEN_ENDPOINT_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".localdomain",
    ".home.arpa",
)


def is_acceptable_push_endpoint(endpoint: str) -> bool:
    """Отсекает endpoint, по которому сервер не должен отправлять запросы.

    Адрес приходит от браузера, поэтому он недоверенный: без проверки сервер
    превращается в SSRF-прокси к localhost, служебным адресам и внутренней сети.
    """
    if not isinstance(endpoint, str) or len(endpoint) > 1000:
        return False
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        return False

    host = parsed.hostname.strip().rstrip(".").lower()
    if not host or host in FORBIDDEN_ENDPOINT_HOSTS:
        return False
    if host.endswith(FORBIDDEN_ENDPOINT_SUFFIXES):
        return False

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Не литерал адреса: требуем доменное имя с точкой и без пробелов.
        return "." in host and not any(char.isspace() for char in host)
    return address.is_global


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


def send_direct_message_push(*, recipient_id: int, room_slug: str, sender_id: int | None = None) -> int:
    """Доставляет нейтральное уведомление и удаляет мёртвые endpoint."""
    from users.services.safety import pair_blocked
    if sender_id and pair_blocked(sender_id, recipient_id):
        return 0
    if not settings.WEB_PUSH_ENABLED:
        return 0

    # Тихие часы и присутствие проверяются здесь, а не у вызывающего:
    # уведомление шлётся из нескольких мест — WebSocket, REST и отложенная
    # задача, — и правило, разбросанное по ним, рано или поздно разойдётся.
    from users.models import User
    from users.services.notifications import KIND_DIRECT, should_notify, subscriptions_for

    recipient = User.objects.filter(pk=recipient_id).first()
    if recipient is None:
        return 0

    allowed, reason = should_notify(recipient, kind=KIND_DIRECT)
    if not allowed:
        logger.info(
            "Личное уведомление не отправлено получателю %s: %s",
            recipient_id,
            reason,
        )
        return 0

    payload = {
        "title": "Новое личное сообщение",
        "body": "В Самогоне ждёт личная реплика.",
        "url": reverse("chat:chat", args=[room_slug]),
        "tag": f"direct-message-{room_slug}",
    }
    return send_push_payload(
        subscriptions=subscriptions_for(user=recipient, kind=KIND_DIRECT),
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


def send_moderator_report_push() -> PushDeliveryResult:
    """Нейтрально уведомляет все устройства модераторов и суперпользователей."""
    moderators = User.objects.filter(
        Q(is_superuser=True)
        | Q(
            user_permissions__content_type__app_label="chat",
            user_permissions__codename="view_messagereport",
        )
        | Q(
            groups__permissions__content_type__app_label="chat",
            groups__permissions__codename="view_messagereport",
        )
    ).distinct()
    return send_push_payload(
        # Служебное уведомление, но «выключено» значит выключено: если
        # человек попросил не беспокоить, модераторская жалоба — не повод
        # сделать исключение. Тихие часы здесь ни при чём: жалоба требует
        # действия, и утром её будет видно в интерфейсе.
        subscriptions=PushSubscription.objects.filter(user__in=moderators).exclude(
            mode=PushSubscription.Mode.OFF
        ),
        payload={
            "title": "Новая жалоба",
            "body": "В Самогоне появилась новая жалоба.",
            "url": reverse("moderation"),
            "tag": "moderation-report",
        },
    )


def enqueue_direct_message_push(
    *,
    recipient_id: int,
    room_slug: str,
    sender_id: int | None = None,
) -> bool:
    """Ставит уведомление в очередь, не задерживая ответ WebSocket.

    Если брокер недоступен, отправляем сразу в этом же процессе: лучше
    задержать один запрос, чем потерять уведомление молча.
    """
    if not settings.WEB_PUSH_ENABLED:
        return False

    from users.tasks import deliver_direct_message_push

    try:
        deliver_direct_message_push.delay(
            recipient_id=recipient_id,
            room_slug=room_slug,
            sender_id=sender_id,
        )
    except Exception:
        logger.warning("direct_message_push_dispatch_failed")
        send_direct_message_push(
            recipient_id=recipient_id,
            room_slug=room_slug,
            sender_id=sender_id,
        )
        return False
    return True


def send_room_message_push(*, room_slug: str, room_name: str, sender_id: int) -> int:
    """Сообщает о новом сообщении в открытой комнате.

    Кому: **всем, кроме отправителя**, кто выбрал «все новые сообщения».
    Списка участников у открытой комнаты нет — в неё заходят все, — поэтому
    и получатели это все, кто ею пользуется.

    Почему это не превращается в поток уведомлений:

    * **присутствие** — кто сидит в приложении, тому не шлём: он и так
      видит сообщение. Это и есть ответ на «дёргают, пока я читаю»;
    * **тихие часы** — ночью не шлём никому;
    * **склейка** — у уведомлений один ``tag`` на комнату, и десять
      сообщений подряд заменяют друг друга, а не висят десятью строками.

    Имя отправителя в уведомление не попадает: его увидят на экране
    блокировки, а текст сообщения — тем более.
    """
    from users.models import PushSubscription, User
    from users.services.notifications import KIND_ROOM, should_notify, subscriptions_for

    if not settings.WEB_PUSH_ENABLED:
        return 0

    recipients = (
        User.objects.filter(is_active=True, push_subscriptions__mode=PushSubscription.Mode.ALL)
        .exclude(pk=sender_id)
        .exclude(username=settings.BARTENDER_USERNAME)
        .distinct()
    )

    delivered = 0
    for recipient in recipients:
        allowed, reason = should_notify(recipient, kind=KIND_ROOM)
        if not allowed:
            logger.info(
                "Сообщение из комнаты %s не отправлено получателю %s: %s",
                room_slug,
                recipient.pk,
                reason,
            )
            continue
        delivered += send_push_payload(
            subscriptions=subscriptions_for(user=recipient, kind=KIND_ROOM),
            payload={
                "title": f"Новое в «{room_name}»",
                "body": "В Самогоне новое сообщение в общей комнате.",
                "url": reverse("chat:chat", args=[room_slug]),
                "tag": f"room-{room_slug}",
            },
        ).delivered
    return delivered


def enqueue_room_message_push(*, room_slug: str, room_name: str, sender_id: int) -> bool:
    """Ставит уведомление о сообщении в комнате в очередь.

    Из WebSocket — обязательно через очередь: рассылка обходит всех
    получателей по очереди, и держать ею ответ сокета нельзя.
    """
    if not settings.WEB_PUSH_ENABLED:
        return False

    from users.tasks import deliver_room_message_push

    try:
        deliver_room_message_push.delay(
            room_slug=room_slug,
            room_name=room_name,
            sender_id=sender_id,
        )
    except Exception:
        logger.warning("room_message_push_dispatch_failed")
        send_room_message_push(
            room_slug=room_slug,
            room_name=room_name,
            sender_id=sender_id,
        )
        return False
    return True


def enqueue_moderator_report_push() -> bool:
    """Ставит уведомление модераторам в очередь.

    Вызывается после фиксации транзакции: сеть не должна держать ни её, ни
    блокировку строки сообщения. Раньше запросы ко всем модераторам шли
    прямо внутри транзакции.
    """
    if not settings.WEB_PUSH_ENABLED:
        return False

    from users.tasks import deliver_moderator_report_push

    try:
        deliver_moderator_report_push.delay()
    except Exception:
        logger.warning("moderator_report_push_dispatch_failed")
        send_moderator_report_push()
        return False
    return True


def enqueue_admin_push(
    *,
    subscription_ids: list[int],
    title: str,
    body: str,
    url: str,
) -> bool:
    """Ставит общую рассылку в очередь: админка не ждёт push-службу."""
    from users.tasks import deliver_admin_push

    try:
        deliver_admin_push.delay(
            subscription_ids=list(subscription_ids),
            title=title,
            body=body,
            url=url,
        )
    except Exception:
        logger.warning("admin_push_dispatch_failed")
        return False
    return True
