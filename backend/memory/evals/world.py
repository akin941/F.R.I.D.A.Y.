"""The eval workspaces and each case's setup rows (brief D9).

Everything lives in the **eval database** (`config.settings.eval`), under one
synthetic user `EVAL_OWNER_EMAIL`:
- `workspace_ref == "personal"` is that user's personal workspace;
- any other `workspace_ref` is a company workspace `eval:<ref>` they own.

Setup rows are loaded as **ordinary rows** (D9 "Eligibility applies to eval
setup rows"): memories `approved` (their `sensitivity` from the case),
facts `approved` and `learned` with a provenance quote, episodes on an
ingestion source of the named kind. So retrieval and the answer call apply
exactly the chat rules: `cloud_eligible()`, `model_eligible()`,
`needs_personal_data()`. Memories are embedded through the production
embedding path (`memory.tasks.embed_memory`), which embeds only
`cloud_eligible()` rows.

`clear_case_rows()` deletes only the setup rows (memories, facts, entities,
episodes) of the eval user's workspaces; it never touches `ModelUsage`, the
audit log or any other user's workspace.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from memory.graph.candidates import normalise_name
from memory.graph.services import as_instant
from memory.ingestion.scrubber import scrub_text
from memory.models import (
    Episode,
    IngestionSource,
    Memory,
    MemoryEntity,
    MemoryFact,
    compute_fingerprint,
)
from memory.tasks import embed_memory
from workspaces.models import Workspace, WorkspaceMember
from workspaces.services import ensure_personal_workspace

EVAL_OWNER_EMAIL = "eval-owner@friday-eval.invalid"
FIRM_PREFIX = "eval:"
EPISODE_TTL = timedelta(days=30)


class EvalWorld:
    def __init__(self, owner, personal):
        self.owner = owner
        self.personal = personal
        self._workspaces = {"personal": personal}
        self._sources = {}

    def workspace(self, ref):
        if ref not in self._workspaces:
            workspace = Workspace.objects.filter(
                owner=self.owner, type=Workspace.Type.COMPANY, name=f"{FIRM_PREFIX}{ref}"
            ).first()
            if workspace is None:
                workspace = Workspace.objects.create(
                    name=f"{FIRM_PREFIX}{ref}", type=Workspace.Type.COMPANY, owner=self.owner
                )
            WorkspaceMember.objects.get_or_create(
                workspace=workspace,
                user=self.owner,
                defaults={"role": WorkspaceMember.Role.OWNER},
            )
            self._workspaces[ref] = workspace
        return self._workspaces[ref]

    def source(self, kind):
        """Ingestion sources live in the personal workspace (8A); an
        episode lands in its own target workspace."""
        if kind not in self._sources:
            self._sources[kind], _ = IngestionSource.objects.get_or_create(
                workspace=self.personal, kind=kind, defaults={"created_by": self.owner}
            )
        return self._sources[kind]

    def workspace_ids(self):
        return list(Workspace.objects.filter(owner=self.owner).values_list("id", flat=True))


def ensure_world():
    user_model = get_user_model()
    owner = user_model.objects.filter(email=EVAL_OWNER_EMAIL).first()
    if owner is None:
        owner = user_model.objects.create_user(email=EVAL_OWNER_EMAIL, name="Eval owner")
    return EvalWorld(owner, ensure_personal_workspace(owner))


@transaction.atomic
def clear_case_rows(world):
    """Delete the previous case's setup rows (D9), nothing else."""
    workspace_ids = world.workspace_ids()
    facts = MemoryFact.all_objects.filter(workspace_id__in=workspace_ids)
    facts.update(supersedes=None, superseded_by=None)
    facts.delete()
    MemoryEntity.all_objects.filter(workspace_id__in=workspace_ids).delete()
    Memory.objects.filter(workspace_id__in=workspace_ids).delete()
    Episode.all_objects.filter(workspace_id__in=workspace_ids).delete()


class LoadedCase:
    """The case's refs mapped to the rows created for it."""

    def __init__(self):
        self.ref_by_id = {}
        self.episodes = []

    def add(self, ref, row):
        self.ref_by_id[str(row.id)] = ref

    def refs(self, ids):
        return [self.ref_by_id.get(str(i)) for i in ids if str(i) in self.ref_by_id]


def _entity(world, workspace, ref):
    entity, _ = MemoryEntity.all_objects.get_or_create(
        workspace=workspace,
        kind=ref.kind,
        normalised_name=normalise_name(ref.name),
        origin=MemoryEntity.Origin.LEARNED,
        defaults={"name": ref.name},
    )
    return entity


def load_case(world, case):
    """Create the case's setup rows and embed its memories. Returns a
    `LoadedCase`."""
    loaded = LoadedCase()
    with transaction.atomic():
        memory_ids = []
        for row in case.memories:
            workspace = world.workspace(row.workspace_ref)
            memory = Memory.objects.create(
                workspace=workspace,
                memory_type=row.type,
                sensitivity=row.sensitivity,
                status=Memory.Status.APPROVED,
                source=Memory.Source.USER_CREATED,
                title=row.title,
                content=row.content,
                fingerprint=compute_fingerprint(f"{row.ref}\n{row.title}\n{row.content}"),
                created_by=world.owner,
            )
            if row.updated_at is not None:
                Memory.objects.filter(pk=memory.pk).update(updated_at=as_instant(row.updated_at))
            loaded.add(row.ref, memory)
            memory_ids.append(memory.id)

        facts = {}
        for row in case.facts:
            workspace = world.workspace(row.workspace_ref)
            subject = _entity(world, workspace, row.subject)
            obj = _entity(world, workspace, row.object) if row.object is not None else None
            fact = MemoryFact.all_objects.create(
                workspace=workspace,
                subject=subject,
                predicate=row.predicate,
                object_entity=obj,
                object_text="" if obj is not None else row.object_text,
                statement=row.statement,
                valid_from=as_instant(row.valid_from),
                valid_to=as_instant(row.valid_to) if row.valid_to is not None else None,
                status=MemoryFact.Status.APPROVED,
                origin=MemoryFact.Origin.LEARNED,
                sensitivity=row.sensitivity,
                provenance_quote=row.quote,
                created_by=world.owner,
            )
            facts[row.ref] = fact
            loaded.add(row.ref, fact)
        for row in case.facts:
            if row.superseded_by is not None:
                MemoryFact.all_objects.filter(pk=facts[row.ref].pk).update(
                    superseded_by=facts[row.superseded_by]
                )

        now = timezone.now()
        for index, row in enumerate(case.episodes):
            workspace = world.workspace(row.workspace_ref)
            episode = Episode.all_objects.create(
                workspace=workspace,
                source=world.source(row.source),
                kind=row.kind,
                external_id=f"eval:{case.id}:{index}",
                occurred_at=as_instant(row.occurred_at),
                # Scrubbed like `record_episode` does (counts are not kept).
                owner_text=scrub_text(row.owner_text).text,
                context_text=scrub_text(row.context_text).text,
                sensitivity=row.sensitivity,
                expires_at=now + EPISODE_TTL,
            )
            loaded.add(row.ref, episode)
            loaded.episodes.append(episode)

    for memory_id in memory_ids:
        # The production path: claims, embeds only cloud_eligible() rows.
        embed_memory(str(memory_id))
    return loaded
