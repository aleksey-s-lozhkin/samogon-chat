from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0010_message_reply_to"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MessageReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reason", models.CharField(choices=[("abuse", "Оскорбление или травля"), ("spam", "Спам или реклама"), ("privacy", "Личные данные"), ("other", "Другое")], max_length=16)),
                ("details", models.CharField(blank=True, max_length=240)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("message", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reports", to="chat.message")),
                ("reporter", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="message_reports", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.AddConstraint(
            model_name="messagereport",
            constraint=models.UniqueConstraint(fields=("message", "reporter"), name="unique_message_reporter"),
        ),
    ]
