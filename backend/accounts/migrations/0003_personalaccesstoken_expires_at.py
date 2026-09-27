from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone

import accounts.models

# Tokens minted before PATs could expire get a short grace period rather
# than the full default lifetime (Phase 4 branch 1, brief Q11).
_BACKFILL_DAYS = 30


def backfill_expires_at(apps, schema_editor):
    PersonalAccessToken = apps.get_model("accounts", "PersonalAccessToken")
    PersonalAccessToken.objects.filter(expires_at__isnull=True).update(
        expires_at=timezone.now() + timedelta(days=_BACKFILL_DAYS)
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_personalaccesstoken"),
    ]

    operations = [
        migrations.AddField(
            model_name="personalaccesstoken",
            name="expires_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.RunPython(backfill_expires_at, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="personalaccesstoken",
            name="expires_at",
            field=models.DateTimeField(default=accounts.models.default_pat_expiry),
        ),
    ]
