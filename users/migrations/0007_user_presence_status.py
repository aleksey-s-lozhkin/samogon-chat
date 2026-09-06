from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0006_user_welcome_pending"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="presence_status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("reading", "Читаю, но не отвечаю"),
                    ("eating", "Кушаю"),
                    ("beer", "Пью пиво"),
                    ("thinking", "Думаю"),
                    ("smoking", "Ушёл курить"),
                    ("back_soon", "Скоро вернусь"),
                ],
                max_length=24,
                verbose_name="Статус в чате",
            ),
        ),
    ]
