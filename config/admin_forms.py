from django import forms

from chat.models import Room


class MessageExportForm(forms.Form):
    period = forms.ChoiceField(
        label="Период",
        choices=(("7", "Последние 7 дней"), ("30", "Последние 30 дней"), ("all", "За всё время")),
        initial="30",
    )
    room = forms.ModelChoiceField(
        label="Комната",
        queryset=Room.objects.order_by("name"),
        required=False,
        empty_label="Все комнаты",
    )
    file_format = forms.ChoiceField(
        label="Формат файла",
        choices=(("jsonl", "JSONL для анализа"), ("csv", "CSV")),
        initial="jsonl",
    )
    include_content = forms.BooleanField(
        label="Добавить текст сообщений",
        required=False,
        help_text="Текст может содержать персональные данные.",
    )
    redact_content = forms.BooleanField(
        label="Скрыть email, IP, ссылки и упоминания",
        required=False,
        initial=True,
    )
    include_private = forms.BooleanField(
        label="Добавить личные сообщения и закрытые комнаты",
        required=False,
        help_text="Включайте только когда это действительно нужно для разбора.",
    )
    include_hidden = forms.BooleanField(
        label="Добавить скрытые модератором сообщения",
        required=False,
    )
