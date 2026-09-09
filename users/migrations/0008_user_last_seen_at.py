from django.db import migrations, models


def seed_last_seen_from_login(apps, schema_editor):
    user_model = apps.get_model("users", "User")
    user_model.objects.filter(last_login__isnull=False).update(
        last_seen_at=models.F("last_login")
    )


class Migration(migrations.Migration):
    dependencies = [("users", "0007_user_presence_status")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="last_seen_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="Последняя активность в чате",
            ),
        ),
        migrations.RunPython(seed_last_seen_from_login, migrations.RunPython.noop),
    ]
