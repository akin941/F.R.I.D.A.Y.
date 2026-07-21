# HarnessRepo is deliberately NOT registered in the Django admin, the same
# convention tasks/memory/knowledge use: admin querysets bypass for_actor()
# scoping and the curator-role checks in harness.services. Registering a
# repo grants harness access to a real git URL -- all access goes through
# the audited service layer, never the admin's raw CRUD.
