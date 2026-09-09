from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model

from .models import Room


User = get_user_model()


class PrivateRoomForm(forms.Form):
    """Создаёт или изменяет закрытую беседу владельца."""

    name = forms.CharField(
        label="Название",
        max_length=100,
        help_text="Например: «Обсудим релиз».",
    )
    members = forms.ModelMultipleChoiceField(
        label="Кого добавить",
        queryset=User.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Выберите одного или двух участников.",
    )

    def __init__(self, *args, user=None, room=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.room = room
        if user and user.is_authenticated:
            self.fields["members"].queryset = User.objects.filter(
                is_active=True,
            ).exclude(
                id=user.id,
            ).exclude(
                username=settings.BARTENDER_USERNAME,
            ).exclude(
                is_superuser=True,
            ).order_by("username")
        if room and not self.is_bound:
            self.initial["name"] = room.name
            self.initial["members"] = room.members.exclude(id=user.id)

    def clean_members(self):
        members = self.cleaned_data["members"]
        if not members:
            raise forms.ValidationError(
                "Добавьте хотя бы одного участника в закрытую беседу."
            )
        if members.count() > 2:
            raise forms.ValidationError(
                "В закрытой беседе может быть только два приглашённых участника."
            )
        return members


class MessageSearchForm(forms.Form):
    q = forms.CharField(
        label="Поиск по сообщениям",
        min_length=2,
        max_length=100,
        widget=forms.SearchInput(
            attrs={"placeholder": "Текст сообщения…", "autofocus": True},
        ),
    )
