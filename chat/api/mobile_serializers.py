from rest_framework import serializers
from .serializers import RoomSerializer


class MobileErrorSerializer(serializers.Serializer):
    error = serializers.CharField(required=False)
    fields = serializers.DictField(required=False)
    detail = serializers.CharField(required=False)


class AccountSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    display_name = serializers.CharField()


class RoomWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    member_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), min_length=1, max_length=2)

    def validate_member_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Участники не должны повторяться.")
        return value


class RoomDetailSerializer(RoomSerializer):
    owner = AccountSerializer(allow_null=True)
    members = AccountSerializer(many=True)
    can_manage = serializers.BooleanField()
    can_leave = serializers.BooleanField()
    can_delete = serializers.BooleanField()


class GuestSerializer(AccountSerializer):
    avatar_url = serializers.CharField(allow_null=True)
    status_code = serializers.CharField(allow_blank=True)
    status = serializers.CharField(allow_blank=True)
    last_seen_at = serializers.DateTimeField(allow_null=True)


class GuestsSerializer(serializers.Serializer):
    api_version = serializers.CharField()
    guests = GuestSerializer(many=True)
    online = serializers.ListField(child=serializers.CharField())
    has_more = serializers.BooleanField()
    next_offset = serializers.IntegerField(allow_null=True)


class StatusOptionSerializer(serializers.Serializer):
    code = serializers.CharField()
    label = serializers.CharField()


class StatusListSerializer(serializers.Serializer):
    api_version = serializers.CharField()
    statuses = StatusOptionSerializer(many=True)
