from django.contrib import admin

from .models import Message, Room, RoomMembership, RoomReadState


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "visibility", "owner", "created_at")
    list_filter = ("visibility",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("user", "room", "text", "created_at")
    list_filter = ("room",)


@admin.register(RoomMembership)
class RoomMembershipAdmin(admin.ModelAdmin):
    list_display = ("room", "user", "joined_at")


@admin.register(RoomReadState)
class RoomReadStateAdmin(admin.ModelAdmin):
    list_display = ("room", "user", "last_read_at")
