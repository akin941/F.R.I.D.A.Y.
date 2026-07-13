from skills.catalog import SkillSpec, SkillStep

SPEC = SkillSpec(
    name="note_to_task",
    description="Turn a referenced note into a follow-up task.",
    trigger_hint="e.g. 'turn this note into a task', 'bu notu görev haline getir'",
    steps=(
        SkillStep(
            kind="read",
            description="Retrieve and summarize the referenced note",
            payload={},
        ),
        SkillStep(
            kind="task_draft",
            description="Draft a follow-up task from the summary",
            payload={"title": "Follow up on note", "due_date": None},
        ),
    ),
)
