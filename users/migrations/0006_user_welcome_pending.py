from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0005_pushsubscription"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="welcome_pending",
            field=models.BooleanField(default=False),
        ),
    ]
