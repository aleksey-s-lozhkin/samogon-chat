from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("chat", "0013_bartenderjob")]

    operations = [
        migrations.AlterField(
            model_name="messagereaction",
            name="emoji",
            field=models.CharField(
                choices=[
                    ("👍", "Нравится"),
                    ("👎", "Не нравится"),
                    ("❤️", "Любовь"),
                    ("😂", "Смешно"),
                    ("🔥", "Огонь"),
                    ("😮", "Удивлён"),
                    ("😢", "Грустно"),
                    ("🤔", "Задумался"),
                    ("🤝", "Согласен"),
                    ("🎉", "Праздную"),
                ],
                max_length=8,
            ),
        ),
    ]
