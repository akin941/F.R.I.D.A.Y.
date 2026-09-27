from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("approvals", "0002_approvalrequest_origin"),
    ]

    operations = [
        migrations.AddField(
            model_name="approvalrequest",
            name="requested_channel",
            field=models.CharField(
                choices=[
                    ("web", "Web"),
                    ("cli", "Cli"),
                    ("telegram", "Telegram"),
                    ("system", "System"),
                    ("unknown", "Unknown"),
                ],
                # Rows created before the channel was recorded.
                default="unknown",
                max_length=20,
            ),
        ),
    ]
