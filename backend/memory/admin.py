# Memory is deliberately NOT registered in the Django admin. Admin querysets
# bypass for_actor() scoping, the workspace role matrix and the memory.read
# audit event (ADR-005: every memory access is auditable), and memory rows can
# carry `sensitivity=private` content. All curation goes through the audited
# API service layer; observability for operators comes from the audit log.
