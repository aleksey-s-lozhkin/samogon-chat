from rest_framework import serializers


class ErrorSerializer(serializers.Serializer):
    error = serializers.CharField()


class WebPushStateSerializer(serializers.Serializer):
    configured = serializers.BooleanField()


class StatusSerializer(serializers.Serializer):
    status = serializers.CharField()
    api_version = serializers.CharField()
    authenticated = serializers.BooleanField()
    web_push = WebPushStateSerializer()


class PushSubscriptionSerializer(serializers.Serializer):
    device_id = serializers.CharField()
    enabled = serializers.BooleanField()
    direct_messages_enabled = serializers.BooleanField()
    current_session = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class PushSubscriptionsStateSerializer(WebPushStateSerializer):
    subscriptions = PushSubscriptionSerializer(many=True)


class PushSubscriptionsResponseSerializer(serializers.Serializer):
    api_version = serializers.CharField()
    web_push = PushSubscriptionsStateSerializer()


class PushSelfTestRequestSerializer(serializers.Serializer):
    device_id = serializers.CharField(allow_blank=False)


class PushSelfTestResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=("accepted", "failed"))
    accepted = serializers.IntegerField(min_value=0)
    failed = serializers.IntegerField(min_value=0)
    removed = serializers.IntegerField(min_value=0)


class CurrentUserSerializer(serializers.Serializer):
    username = serializers.CharField()
    avatar_url = serializers.CharField(allow_null=True)
    message_color = serializers.CharField()
    presence_status = serializers.CharField(allow_blank=True)
    presence_status_label = serializers.CharField(allow_blank=True)


class PresenceStatusUpdateSerializer(serializers.Serializer):
    presence_status = serializers.ChoiceField(
        choices=("", "reading", "eating", "beer", "thinking", "smoking", "back_soon"),
        allow_blank=True,
    )
