"""The server-side rules every confirm- and gate-class node action passes
(Phase 10 brief D2, D3, D6, D8), shared by the three places that apply
them: the `node_action` tool's request path (`tools.node_actions`), grant
issuance (`nodes.grants.issue`) and the confirm-class endpoint
(`nodes.services.invoke_confirm`).

- **The actor check** (`actor_is_node_owner`): the actor is an active human
  user, the owner of the node (the user who paired it, Phase 9 D1) and holds
  the `owner` role in the target workspace. It is unconditional for every
  action capability, firm- and personal-class alike.
- **The workspace rule** (`workspace_refusal`, D3): a `repo_path` argument
  (of `repo.command.run` or a repo recipe) must resolve, through the 8A path
  rules, to the target workspace (`path_not_in_workspace`); a
  personal-class capability (AX, apps, a non-repo recipe) is allowed only
  in the owner's personal workspace (`workspace_not_allowed`).
- **The path resolver port.** `nodes` may not import `memory` (import
  contract), so the 8A lookup `memory.ingestion.mapping.workspace_for_path`
  is injected with `register_path_resolver` (by `tools.node_actions`, which
  may import `memory`). Unregistered = no path resolves = refused (fail
  closed).
"""

from collections.abc import Callable

from workspaces.models import Workspace, WorkspaceMember

_path_resolver: Callable | None = None


def register_path_resolver(fn: Callable) -> None:
    """`fn(owner, path) -> Workspace | None`: the 8A path rules."""
    global _path_resolver
    if not callable(fn):
        raise TypeError("the path resolver must be callable")
    _path_resolver = fn


def reset_path_resolver() -> None:
    """Test-only."""
    global _path_resolver
    _path_resolver = None


def path_workspace_id(owner, path):
    """The id of the workspace `path` resolves to for `owner`, or None."""
    if _path_resolver is None or not isinstance(path, str):
        return None
    try:
        workspace = _path_resolver(owner, path)
    except Exception:
        return None
    return getattr(workspace, "pk", None)


def actor_is_node_owner(actor, node, workspace) -> bool:
    """D6/D2/D8: `actor` is active, human, the node's owner and an `owner`
    of `workspace`. False for a missing node, actor or workspace."""
    if actor is None or node is None or workspace is None:
        return False
    if not getattr(actor, "is_authenticated", True) or not getattr(actor, "is_active", False):
        return False
    if getattr(actor, "is_agent", False):
        return False
    if node.owner_id != actor.pk:
        return False
    return WorkspaceMember.objects.filter(
        workspace_id=workspace.pk, user_id=actor.pk, role=WorkspaceMember.Role.OWNER
    ).exists()


def is_owners_personal_workspace(workspace, owner) -> bool:
    return (
        workspace is not None
        and owner is not None
        and workspace.type == Workspace.Type.PERSONAL
        and workspace.owner_id == owner.pk
    )


def repo_path_of(contract, args):
    """The `repo_path` the workspace rule resolves, or None: the path
    argument of a `path_arg` capability, or `params.repo_path` of a repo
    recipe."""
    if not isinstance(args, dict):
        return None
    if contract.workspace_resolution == "path_arg":
        return args.get(contract.path_args[0]) if contract.path_args else None
    if contract.workspace_resolution == "recipe_params":
        recipe = contract.recipe_for(args)
        if recipe is not None and recipe.repo_recipe:
            return (args.get("params") or {}).get("repo_path")
    return None


def workspace_refusal(contract, args, workspace, owner) -> str | None:
    """The D3 workspace rule for one valid request: None, or
    `path_not_in_workspace` / `workspace_not_allowed`."""
    path = repo_path_of(contract, args)
    path_bound = contract.workspace_resolution == "path_arg" or (
        contract.workspace_resolution == "recipe_params" and path is not None
    )
    if path_bound:
        if path is None or path_workspace_id(owner, path) != workspace.pk:
            return "path_not_in_workspace"
        return None
    if not is_owners_personal_workspace(workspace, owner):
        return "workspace_not_allowed"
    return None


def call_data_class(contract, args, workspace) -> str:
    """The data class of one call (D3): a repo recipe carries its
    workspace's class; everything else the contract's."""
    if contract.workspace_resolution == "recipe_params" and repo_path_of(contract, args):
        return "personal" if workspace.type == Workspace.Type.PERSONAL else "firm"
    return contract.data_class
