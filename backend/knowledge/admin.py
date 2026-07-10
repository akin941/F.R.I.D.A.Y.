# Knowledge is deliberately NOT registered in the Django admin, for the same
# reason memory/admin.py gives: admin querysets bypass for_actor() scoping,
# the workspace role matrix and the knowledge.read audit event, and a
# KnowledgeDocument can carry sensitivity=private content. All access goes
# through the audited API service layer; operator observability comes from
# the audit log.
