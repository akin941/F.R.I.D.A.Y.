# TelegramLinkCode/LinkedTelegramChat are deliberately NOT registered in the
# Django admin, the same convention memory/knowledge/tasks/approvals use:
# admin querysets bypass for_actor() scoping and the audited API service
# layer. A link code is a short-lived pairing secret and LinkedTelegramChat
# ties a real Telegram account to a workspace -- both belong exclusively
# behind the integrations views/tasks, never raw admin CRUD.
