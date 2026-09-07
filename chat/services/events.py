from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def message_group_names(message):
    """Возвращает группы Channels только для участников конкретной реплики."""
    if message.recipient_id:
        return {f"chat_user_{message.user_id}", f"chat_user_{message.recipient_id}"}
    if message.room.is_private:
        return {
            f"chat_user_{user_id}"
            for user_id in message.room.memberships.values_list("user_id", flat=True)
        }
    return {f"chat_{message.room.slug}"}


def broadcast_attachment_update(message, attachments):
    """Передаёт новые вложения только тем же людям, что видят сообщение."""
    event = {
        "type": "attachment_update",
        "message_id": message.id,
        "attachments": attachments,
        "room_slug": message.room.slug,
    }
    channel_layer = get_channel_layer()
    for group_name in message_group_names(message):
        async_to_sync(channel_layer.group_send)(group_name, event)
