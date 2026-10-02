# The blind vote ledger: refused votes are kept, withdrawals wait for the daily settlement.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0031_merge_0029_coverage_0030_user_default_profile_hike'),
    ]

    operations = [
        migrations.AddField(
            model_name='coveragevote',
            name='accepted',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='coveragevote',
            name='withdrawn_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
