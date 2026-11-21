"""What producers may call to write the graph (brief D3, Boundary).

Memory owns every learned row. The learning jobs (episode extraction v2 and
the weekly reflection, branch 2) call `propose_fact`; the Graphify importer
in `harness` will call `import_code_graph` (branch 5). Memory never imports
its producers.
"""

from memory.graph import services


def propose_fact(**kwargs):
    """Create one `pending_approval` learned fact; see
    `memory.graph.services.propose_fact`. Never approves."""
    return services.propose_fact(**kwargs)


def import_code_graph(*, workspace, repo_ref, snapshot):
    """The Graphify import (D10): `system` entities and facts, replaced
    temporally per head. Lands in Phase 8B branch 5 (`feat/p8-graphify-job`)."""
    raise NotImplementedError("import_code_graph lands in feat/p8-graphify-job (branch 5).")
