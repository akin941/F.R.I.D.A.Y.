from django.db import migrations


def mark_single_owner(apps, schema_editor):
    """Phase 4 branch 2: an installation with exactly one human (non-agent)
    user has an unambiguous owner -- mark it. Any other shape (no user yet,
    or several humans) is left alone: `bootstrap_owner` sets the flag on a
    fresh install, and an ambiguous database needs an explicit
    `manage.py set_owner --email ...` (issuing pairing codes and enrolling
    devices fail with that instruction until then)."""
    User = apps.get_model("accounts", "User")
    if User.objects.filter(is_owner=True).exists():
        return
    humans = list(User.objects.filter(is_agent=False).values_list("pk", flat=True)[:2])
    if len(humans) == 1:
        User.objects.filter(pk=humans[0]).update(is_owner=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_device_trust"),
    ]

    operations = [
        migrations.RunPython(mark_single_owner, migrations.RunPython.noop),
    ]
