from django.contrib import admin

from .models import Message


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "room_name",
        "text",
        "created_at",
    )
    list_filter = ("room_name", "created_at")
    search_fields = ("text", "user__username")
