from rest_framework import serializers


class ChatApiErrorSerializer(serializers.Serializer):
    error = serializers.CharField()


class RoomSerializer(serializers.Serializer):
    slug = serializers.SlugField()
    name = serializers.CharField()
    description = serializers.CharField()
    visibility = serializers.ChoiceField(choices=("public", "private"))
    unread_count = serializers.IntegerField(min_value=0)


class RoomsResponseSerializer(serializers.Serializer):
    api_version = serializers.CharField()
    rooms = RoomSerializer(many=True)


class MessageSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    avatar_url = serializers.CharField(allow_null=True)
    message = serializers.CharField()
    created_at = serializers.DateTimeField()
    recipient = serializers.CharField(allow_null=True)
    private = serializers.BooleanField()
    color = serializers.CharField()
    attachments = serializers.ListField()
    reactions = serializers.ListField()
    reply_to = serializers.DictField(allow_null=True)


class MessagesResponseSerializer(serializers.Serializer):
    api_version = serializers.CharField()
    room = RoomSerializer()
    messages = MessageSerializer(many=True)


class MessageCreateSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=1000, trim_whitespace=True)
    recipient = serializers.CharField(required=False, allow_null=True, allow_blank=False)
    reply_to = serializers.IntegerField(required=False, allow_null=True, min_value=1)


class ReactionToggleSerializer(serializers.Serializer):
    emoji = serializers.ChoiceField(
        choices=("👍", "👎", "❤️", "😂", "🔥", "😮", "😢", "🤔", "🤝", "🎉")
    )


class ReactionSerializer(serializers.Serializer):
    message_id = serializers.IntegerField()
    emoji = serializers.CharField()
    count = serializers.IntegerField(min_value=0)
    active = serializers.BooleanField()
    users = serializers.ListField(child=serializers.CharField())


class MessageReportCreateSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(choices=("abuse", "spam", "privacy", "other"))
    details = serializers.CharField(required=False, allow_blank=True, max_length=240)


class MessageReportSerializer(serializers.Serializer):
    reported = serializers.BooleanField()
    created = serializers.BooleanField()


class AttachmentUploadSerializer(serializers.Serializer):
    files = serializers.ListField(
        child=serializers.FileField(),
        min_length=1,
        max_length=3,
    )


class AttachmentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    size = serializers.IntegerField(min_value=1)
    kind = serializers.ChoiceField(choices=("image", "file"))
    preview_url = serializers.CharField()
    download_url = serializers.CharField()


class AttachmentsResponseSerializer(serializers.Serializer):
    attachments = AttachmentSerializer(many=True)


class NoteCreateSerializer(serializers.Serializer):
    text = serializers.CharField(required=False, allow_blank=False, max_length=1000)
    source_message_id = serializers.IntegerField(required=False, min_value=1)

    def validate(self, attrs):
        if ("text" in attrs) == ("source_message_id" in attrs):
            raise serializers.ValidationError(
                "Specify exactly one of text or source_message_id."
            )
        return attrs


class NoteSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    text = serializers.CharField()
    source_message_id = serializers.IntegerField(allow_null=True)
    source_author = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()
    attachments = AttachmentSerializer(many=True)


class NotesResponseSerializer(serializers.Serializer):
    api_version = serializers.CharField()
    notes = NoteSerializer(many=True)


class NoteMutationResponseSerializer(serializers.Serializer):
    note = NoteSerializer()
    created = serializers.BooleanField()


class BartenderJobCreateSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=1000, trim_whitespace=True)
    private = serializers.BooleanField(default=False)


class BartenderJobSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=("queued", "started", "succeeded", "failed"))
    private = serializers.BooleanField()
    question_message_id = serializers.IntegerField(source="question_id")
    response_message_id = serializers.IntegerField(source="response_id", allow_null=True)
    error = serializers.CharField(source="error_code", allow_blank=True)
    created_at = serializers.DateTimeField()
    started_at = serializers.DateTimeField(allow_null=True)
    finished_at = serializers.DateTimeField(allow_null=True)
