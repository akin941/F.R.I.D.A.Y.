# Approvals are deliberately NOT registered in the Django admin, the same
# convention memory/knowledge/tasks use: admin querysets bypass for_actor()
# scoping, the workspace role matrix, the requester != decider rule, and
# the mutation audit trail. All access goes through the audited API service
# layer.
