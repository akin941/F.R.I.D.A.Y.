# Tasks are deliberately NOT registered in the Django admin, the same
# convention memory/knowledge use: admin querysets bypass for_actor()
# scoping, the workspace role matrix and the mutation audit trail. Task rows
# carry no sensitivity classification, but title/description can still be
# personal or company-sensitive free text -- all access goes through the
# audited API service layer.
