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
