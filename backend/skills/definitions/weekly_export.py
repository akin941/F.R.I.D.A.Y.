from skills.catalog import SkillSpec, SkillStep

SPEC = SkillSpec(
    name="weekly_export",
    description="Summarize recent notes and export them to the Obsidian vault.",
    trigger_hint="e.g. 'export my weekly notes', 'haftalık notlarımı vault'a aktar'",
    steps=(
        SkillStep(kind="read", description="Summarize recent notes", payload={}),
        SkillStep(
            kind="tool_request",
            description="Export the vault",
            payload={"tool_name": "vault_export"},
        ),
    ),
)
