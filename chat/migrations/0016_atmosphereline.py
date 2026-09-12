from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


PEP_20_URL = "https://peps.python.org/pep-0020/"
PEP_20_LINES = (
    "Beautiful is better than ugly.",
    "Explicit is better than implicit.",
    "Simple is better than complex.",
    "Readability counts.",
    "Errors should never pass silently.",
)


def seed_pep_lines(apps, schema_editor):
    AtmosphereLine = apps.get_model("chat", "AtmosphereLine")
    AtmosphereLine.objects.bulk_create(
        [
            AtmosphereLine(
                text=text,
                kind="pep",
                status="approved",
                source_label="PEP 20 — The Zen of Python",
                source_url=PEP_20_URL,
            )
            for text in PEP_20_LINES
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("chat", "0015_closed_conversation_constraint"),
    ]

    operations = [
        migrations.CreateModel(
            name="AtmosphereLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("text", models.CharField(max_length=140, unique=True)),
                ("kind", models.CharField(choices=[("semen", "Реплика Семёна"), ("pep", "Цитата PEP")], max_length=12)),
                ("status", models.CharField(choices=[("draft", "На модерации"), ("approved", "Одобрена"), ("rejected", "Отклонена")], db_index=True, default="draft", max_length=12)),
                ("source_label", models.CharField(blank=True, max_length=80)),
                ("source_url", models.URLField(blank=True)),
                ("generated_by_model", models.CharField(blank=True, max_length=120)),
                ("is_active", models.BooleanField(default=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_atmosphere_lines", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("kind", "text")},
        ),
        migrations.RunPython(seed_pep_lines, migrations.RunPython.noop),
    ]
