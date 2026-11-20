"""Allow or disallow one provider to receive personal-class data (D5,
Phase 5 branch 2, ADR-010 decision 5) -- and, optionally, (de)activate it
in the same audited act (Phase 6 A1/d2: the seeded-disabled OpenRouter
audio rows need both the flag and `is_active` before the gateway can
select them).

`ModelProvider.personal_data_allowed` decides whether a gateway call made
with `personal_data=True` (mail, calendar, Home Assistant, sensitive-repo
reads, and every STT/TTS call) may reach that provider. `seed_ai_defaults`
sets it only for the local, no-network providers it creates; this command
is the one sanctioned way to change it afterwards (the admin shows it
read-only), and every change -- a no-op included, so an attempted flip is
never silent -- writes an `ai_core.provider_personal_data_set` audit row.

    manage.py set_provider_personal_data <provider-name> --allow
    manage.py set_provider_personal_data <provider-name> --deny
    manage.py set_provider_personal_data openrouter-tts --allow --activate
    manage.py set_provider_personal_data openrouter-tts --deny --deactivate
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ai_core.models import ModelProvider

# ADR-003 keeps ai_core free of business-app imports; this operator command
# is the one narrow exception (pyproject.toml `ignore_imports`), because D5
# requires the flip to be audited and `record_audit` is the only sanctioned
# audit writer. No gateway/runtime code path imports this module.
from audit.services import record_audit


class Command(BaseCommand):
    help = "Allow or deny personal-class data for one ModelProvider (audited)."

    def add_arguments(self, parser):
        parser.add_argument("provider", help="ModelProvider.name, e.g. ollama-local")
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--allow", action="store_true", help="Permit personal-class data.")
        group.add_argument("--deny", action="store_true", help="Refuse personal-class data.")
        active = parser.add_mutually_exclusive_group()
        active.add_argument(
            "--activate", action="store_true", help="Also set is_active=True (d2 toggle)."
        )
        active.add_argument("--deactivate", action="store_true", help="Also set is_active=False.")

    def handle(self, *args, **options):
        allowed = bool(options["allow"])
        activate = None
        if options["activate"]:
            activate = True
        elif options["deactivate"]:
            activate = False
        with transaction.atomic():
            provider = (
                ModelProvider.objects.select_for_update().filter(name=options["provider"]).first()
            )
            if provider is None:
                raise CommandError(f"No ModelProvider named {options['provider']!r}.")
            previous = provider.personal_data_allowed
            previous_active = provider.is_active
            fields = []
            if previous != allowed:
                provider.personal_data_allowed = allowed
                fields.append("personal_data_allowed")
            if activate is not None and previous_active != activate:
                provider.is_active = activate
                fields.append("is_active")
            if fields:
                provider.save(update_fields=[*fields, "updated_at"])
            metadata = {
                "via": "management_command",
                "provider": provider.name,
                "provider_type": provider.provider_type,
                "previous": previous,
                "personal_data_allowed": allowed,
                "changed": previous != allowed,
            }
            if activate is not None:
                metadata.update(
                    {
                        "previous_is_active": previous_active,
                        "is_active": activate,
                        "active_changed": previous_active != activate,
                    }
                )
            record_audit(
                action="ai_core.provider_personal_data_set",
                resource=provider,
                metadata=metadata,
            )
        state = "allowed" if allowed else "denied"
        suffix = "" if previous != allowed else " (unchanged)"
        if activate is not None:
            suffix += f"; {'active' if activate else 'inactive'}"
        self.stdout.write(
            self.style.SUCCESS(f"{provider.name}: personal-class data {state}{suffix}.")
        )
