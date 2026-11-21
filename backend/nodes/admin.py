# No Django admin for nodes by design: the admin bypasses `for_actor()`
# workspace scoping (ADR-001; see approvals/admin.py for the same argument),
# and `NodeInvocation` is append-only. Nodes are read through the scoped
# node APIs (branches 4/5) and `manage.py verify_node_audit_chain`.
