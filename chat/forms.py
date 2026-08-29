from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model

from .models import Room


User = get_user_model()


class PrivateRoomForm(forms.Form):
    """Форма создания личного столика максимум для трёх гостей."""

    name = forms.CharField(
        label="Название",
        max_length=100,
        help_text="Например: «Сообразим на троих».",
    )
    members = forms.ModelMultipleChoiceField(
        label="Кого позвать",
        queryset=User.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Можно пригласить не больше двух человек.",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and user.is_authenticated:
            self.fields["members"].queryset = User.objects.filter(
                is_active=True,
            ).exclude(
                id=user.id,
            ).exclude(
                username=settings.BARTENDER_USERNAME,
            ).order_by("username")

    def clean_members(self):
        members = self.cleaned_data["members"]
        if members.count() > 2:
            raise forms.ValidationError(
                "У тайного столика может быть только два приглашённых гостя."
            )
        return members
