"""What producers may call to write the graph (brief D3, Boundary).

Memory owns every learned row. The learning jobs (episode extraction v2 and
the weekly reflection, branch 2) call `propose_fact`; the Graphify importer
in `harness` calls `import_code_graph` and `retire_code_graph` (branch 5).
Memory never imports its producers.
"""

from memory.graph import code_graph, services
from memory.graph.code_graph import CodeGraphInvalid

__all__ = ["CodeGraphInvalid", "import_code_graph", "propose_fact", "retire_code_graph"]


def propose_fact(**kwargs):
    """Create one `pending_approval` learned fact; see
    `memory.graph.services.propose_fact`. Never approves."""
    return services.propose_fact(**kwargs)


def import_code_graph(*, workspace, repo_ref, commit_sha, committed_at, graph):
    """The Graphify import (D10): `system` entities and facts of one repo,
    replaced temporally per head. `graph` is the producer's mapped snapshot
    (`{"entities": [{key, kind, name}], "facts": [{subject, predicate,
    object}]}`); see `memory.graph.code_graph.import_code_graph`. Raises
    `CodeGraphInvalid` with nothing changed."""
    return code_graph.import_code_graph(
        workspace=workspace,
        repo_ref=repo_ref,
        commit_sha=commit_sha,
        committed_at=committed_at,
        graph=graph,
    )


def retire_code_graph(*, workspace, repo_ref, actor=None):
    """The repo's `graphify_enabled` switch turned off (D7): close its
    current `system` facts, hide its entities, delete nothing."""
    return code_graph.retire_code_graph(workspace=workspace, repo_ref=repo_ref, actor=actor)
