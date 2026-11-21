"""Phase 10 brief D9: the `control` AIBudget row (5 USD/month by default)
over the node action trust check (`control.trust_check`).

Created once, like the `seed_ai_defaults` budgets: an existing row (the
owner edited it in the admin) is never touched. Reverse is a no-op so a
rollback never deletes an owner-edited cap."""

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import migrations

CONTROL_BUDGET = "control"
CONTROL_PURPOSES = ["control.trust_check"]


def _monthly_limit():
    raw = getattr(settings, "AI_BUDGET_CONTROL_MONTHLY_USD", "5")
    if raw in (None, ""):
        return None
    try:
        amount = Decimal(str(raw))
    except InvalidOperation:
        return Decimal("5")
    if not amount.is_finite() or amount < 0:
        return Decimal("5")
    return amount


def create_control_budget(apps, schema_editor):
    AIBudget = apps.get_model("ai_core", "AIBudget")
    AIBudget.objects.get_or_create(
        name=CONTROL_BUDGET,
        defaults={"purposes": list(CONTROL_PURPOSES), "monthly_limit_usd": _monthly_limit()},
    )


class Migration(migrations.Migration):
    dependencies = [
        ("ai_core", "0007_speech_gateway_budgets"),
    ]

    operations = [
        migrations.RunPython(create_control_budget, migrations.RunPython.noop),
    ]
