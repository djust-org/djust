from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies: list = []

    operations = [
        migrations.CreateModel(
            name="ViewState",
            fields=[
                ("key", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("payload", models.BinaryField()),
                ("expires", models.BigIntegerField(db_index=True)),
            ],
            options={
                "verbose_name": "view state",
                "db_table": "djust_view_state",
            },
        ),
    ]
