# VaultExportRun is deliberately NOT registered in the Django admin, the
# same convention memory/knowledge/tasks/approvals use: admin querysets
# bypass for_actor() scoping and the audited service layer (finalize_vault_
# export_success/failure). All access goes through the lease-fenced claim/
# finalize functions in tools/export.py.
