"""Node action tasks (Phase 10 brief D6, D7). Autodiscovered by
`config.celery.app.autodiscover_tasks()`.

- `run_node_action(execution_id)`: dispatched by the `node_action` tool on
  approval (`transaction.on_commit`). It issues the grant and pushes the
  envelope, then returns; it never waits for the node. No retry, ever
  (ADR-013 decision 6): a failure is a terminal status and a new attempt is
  a new proposal.
- `sweep_node_actions_task` (beat, every minute) and
  `clear_node_action_tails_task` (beat, daily): the backstops.
- `nodes.signing_key_id` (Phase 9 closeout): the worker's configured node
  signing key id -- the id only, never key material -- so `manage.py
  preflight` (`node_signing_key`) can check that web, worker and
  node-gateway sign with the same key.
"""

from celery import shared_task

from nodes import keys, services


@shared_task(acks_late=False, max_retries=0)
def run_node_action(execution_id):
    execution = services.dispatch_gated(execution_id)
    return getattr(execution, "status", None)


@shared_task
def sweep_node_actions_task():
    return services.sweep_node_actions()


@shared_task
def clear_node_action_tails_task():
    return services.clear_expired_tails()


@shared_task(name="nodes.signing_key_id", acks_late=False, max_retries=0, ignore_result=False)
def signing_key_id_task():
    """The key id this worker would sign with, or None when no usable key
    is configured (missing, invalid or a published test key)."""
    return keys.signing_key_id()
