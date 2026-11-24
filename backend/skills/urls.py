from django.urls import path

from skills import views

urlpatterns = [
    path("skills", views.SkillListView.as_view(), name="skills-list"),
    path("skills/upload", views.SkillUploadView.as_view(), name="skills-upload"),
    path("skills/curator", views.CuratorView.as_view(), name="skills-curator"),
    path(
        "skills/curator/dismiss",
        views.CuratorDismissView.as_view(),
        name="skills-curator-dismiss",
    ),
    path(
        "skills/versions/<uuid:version_id>/approve",
        views.VersionApproveView.as_view(),
        name="skills-version-approve",
    ),
    path(
        "skills/versions/<uuid:version_id>/edit-approve",
        views.VersionEditApproveView.as_view(),
        name="skills-version-edit-approve",
    ),
    path(
        "skills/versions/<uuid:version_id>/reject",
        views.VersionRejectView.as_view(),
        name="skills-version-reject",
    ),
    path("skills/<uuid:skill_id>", views.SkillDetailView.as_view(), name="skills-detail"),
    path(
        "skills/<uuid:skill_id>/versions",
        views.SkillVersionCreateView.as_view(),
        name="skills-versions",
    ),
    path("skills/<uuid:skill_id>/archive", views.SkillArchiveView.as_view(), name="skills-archive"),
    path("skills/<uuid:skill_id>/restore", views.SkillRestoreView.as_view(), name="skills-restore"),
]
