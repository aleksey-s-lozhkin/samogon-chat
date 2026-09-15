from django.db import migrations


PEP_20_URL = "https://peps.python.org/pep-0020/"
SOURCE_LABEL = "PEP 20 — неофициальный перевод"
RUSSIAN_PEP_20_DRAFTS = (
    "Красивое лучше уродливого.",
    "Явное лучше неявного.",
    "Простое лучше сложного.",
    "Сложное лучше запутанного.",
    "Разреженное лучше плотного.",
    "Читаемость имеет значение.",
    "Особые случаи не настолько особые, чтобы нарушать правила.",
    "Практичность важнее безупречности.",
    "Ошибки никогда не должны замалчиваться.",
    "Если только их не замалчивают явно.",
    "При неоднозначности не поддавайтесь искушению угадывать.",
    "Должен быть один — и желательно один — очевидный способ сделать это.",
    "Сейчас лучше, чем никогда.",
    "Хотя никогда часто лучше, чем прямо сейчас.",
    "Если реализацию трудно объяснить — это плохая идея.",
    "Если реализацию легко объяснить — возможно, это хорошая идея.",
    "Пространства имён — отличная идея. Давайте использовать их чаще!",
)


def seed_russian_drafts(apps, schema_editor):
    AtmosphereLine = apps.get_model("chat", "AtmosphereLine")
    AtmosphereLine.objects.bulk_create([
        AtmosphereLine(
            text=text,
            kind="pep",
            status="draft",
            source_label=SOURCE_LABEL,
            source_url=PEP_20_URL,
        )
        for text in RUSSIAN_PEP_20_DRAFTS
    ], ignore_conflicts=True)


def remove_russian_drafts(apps, schema_editor):
    AtmosphereLine = apps.get_model("chat", "AtmosphereLine")
    AtmosphereLine.objects.filter(
        text__in=RUSSIAN_PEP_20_DRAFTS,
        kind="pep",
        status="draft",
        source_label=SOURCE_LABEL,
        source_url=PEP_20_URL,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("chat", "0017_audio_attachments")]

    operations = [
        migrations.RunPython(seed_russian_drafts, remove_russian_drafts),
    ]
