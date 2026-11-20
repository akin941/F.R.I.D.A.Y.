"""Jarvis -> Friday rename (2026-11-20): stored prompt templates.

Every `jarvis_*` PromptTemplate becomes `friday_*` (the names the code now
reads). Prompt versions are immutable by discipline, so a template whose
ACTIVE version still names the assistant "Jarvis" gets a new version with
the name replaced and is repointed to it; the old version stays, so
historical `ModelUsage.prompt_version` rows keep pointing at what was
actually sent. A fresh database has nothing to rename (`seed_ai_defaults`
seeds the `friday_*` names directly), and re-running is a no-op.
"""

from django.db import migrations

LEGACY_PREFIX = "jarvis_"
PREFIX = "friday_"
_NAME_REPLACEMENTS = (("JARVIS", "FRIDAY"), ("Jarvis", "Friday"), ("jarvis", "friday"))
CHANGELOG = "Assistant renamed from Jarvis to Friday (2026-11-20, data migration)."


def _renamed(text):
    for old, new in _NAME_REPLACEMENTS:
        text = text.replace(old, new)
    return text


def rename_templates(apps, schema_editor):
    PromptTemplate = apps.get_model("ai_core", "PromptTemplate")
    PromptVersion = apps.get_model("ai_core", "PromptVersion")
    for template in PromptTemplate.objects.filter(name__startswith=LEGACY_PREFIX):
        new_name = PREFIX + template.name[len(LEGACY_PREFIX) :]
        if PromptTemplate.objects.filter(name=new_name).exists():
            continue  # already seeded under the new name; leave the old row alone
        template.name = new_name
        template.save(update_fields=["name"])

        active = template.active_version_id and PromptVersion.objects.get(
            pk=template.active_version_id
        )
        if not active or _renamed(active.content) == active.content:
            continue
        latest = PromptVersion.objects.filter(template=template).order_by("-version").first()
        version = PromptVersion.objects.create(
            template=template,
            version=latest.version + 1,
            content=_renamed(active.content),
            model_params=active.model_params,
            changelog=CHANGELOG,
        )
        template.active_version = version
        template.save(update_fields=["active_version"])


def restore_names(apps, schema_editor):
    """Reverse: the names only. The added versions stay (they are harmless
    history); repoint `active_version` by hand if the old text is wanted."""
    PromptTemplate = apps.get_model("ai_core", "PromptTemplate")
    for template in PromptTemplate.objects.filter(name__startswith=PREFIX):
        old_name = LEGACY_PREFIX + template.name[len(PREFIX) :]
        if not PromptTemplate.objects.filter(name=old_name).exists():
            template.name = old_name
            template.save(update_fields=["name"])


class Migration(migrations.Migration):
    dependencies = [
        ("ai_core", "0005_modelprovider_personal_data_allowed"),
    ]

    operations = [migrations.RunPython(rename_templates, restore_names)]
