from django.db import migrations

import core.models


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [
        migrations.AlterModelManagers(
            name="user",
            managers=[("objects", core.models.UserManager())],
        ),
    ]
