"""Schema validation for `Workspace.profile` (brief §2): a validated
dossier shape, not free-form JSON."""

from workspaces.serializers import WorkspaceProfileUpdateSerializer


def _serializer(profile):
    return WorkspaceProfileUpdateSerializer(data={"profile": profile})


class TestValidProfiles:
    def test_empty_profile_is_valid(self):
        serializer = _serializer({})
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["profile"] == {}

    def test_full_profile_is_accepted(self):
        profile = {
            "repos": [
                {"name": "jarvis-os", "url": "https://github.com/x/jarvis-os"},
                {
                    "name": "external_installation",
                    "url": "https://github.com/x/external_installation",
                    "default_branch": "main",
                },
            ],
            "services": [
                {"name": "web", "kind": "django", "url": "https://web.internal"},
                {"name": "worker"},
            ],
            "deploy_targets": [
                {"name": "prod", "host": "prod.internal", "notes": "blue/green"},
                {"name": "staging"},
            ],
            "notes": "Firm-wide dossier notes.",
        }
        serializer = _serializer(profile)
        assert serializer.is_valid(), serializer.errors
        validated = serializer.validated_data["profile"]
        assert validated["repos"][1]["default_branch"] == "main"
        assert validated["notes"] == "Firm-wide dossier notes."

    def test_optional_subfields_may_be_omitted(self):
        profile = {"repos": [{"name": "jarvis-os", "url": "https://x"}]}
        serializer = _serializer(profile)
        assert serializer.is_valid(), serializer.errors
        assert "default_branch" not in serializer.validated_data["profile"]["repos"][0]


class TestUnknownKeysRejected:
    def test_unknown_top_level_key_rejected(self):
        serializer = _serializer({"secrets": "nope"})
        assert not serializer.is_valid()
        assert "secrets" in str(serializer.errors)

    def test_unknown_repo_entry_key_rejected(self):
        serializer = _serializer(
            {"repos": [{"name": "x", "url": "https://x", "api_key": "sk-shhh"}]}
        )
        assert not serializer.is_valid()
        assert "api_key" in str(serializer.errors)

    def test_unknown_deploy_target_key_rejected(self):
        serializer = _serializer({"deploy_targets": [{"name": "prod", "password": "hunter2"}]})
        assert not serializer.is_valid()
        assert "password" in str(serializer.errors)


class TestWrongTypesRejected:
    def test_profile_must_be_object(self):
        serializer = _serializer("not-an-object")
        assert not serializer.is_valid()

    def test_profile_null_is_rejected(self):
        serializer = _serializer(None)
        assert not serializer.is_valid()

    def test_notes_must_be_string(self):
        serializer = _serializer({"notes": 12345})
        assert not serializer.is_valid()

    def test_repos_must_be_a_list(self):
        serializer = _serializer({"repos": {"name": "x", "url": "https://x"}})
        assert not serializer.is_valid()

    def test_repo_entry_must_be_object(self):
        serializer = _serializer({"repos": ["not-an-object"]})
        assert not serializer.is_valid()

    def test_repo_name_must_be_string_not_int(self):
        serializer = _serializer({"repos": [{"name": 1, "url": "https://x"}]})
        assert not serializer.is_valid()

    def test_repo_missing_required_url_rejected(self):
        serializer = _serializer({"repos": [{"name": "jarvis-os"}]})
        assert not serializer.is_valid()


class TestSizeBounds:
    def test_repos_over_50_items_rejected(self):
        repos = [{"name": f"r{i}", "url": "https://x"} for i in range(51)]
        serializer = _serializer({"repos": repos})
        assert not serializer.is_valid()
        assert "repos" in serializer.errors["profile"]

    def test_repos_exactly_50_items_accepted(self):
        repos = [{"name": f"r{i}", "url": "https://x"} for i in range(50)]
        serializer = _serializer({"repos": repos})
        assert serializer.is_valid(), serializer.errors

    def test_services_over_50_items_rejected(self):
        services = [{"name": f"s{i}"} for i in range(51)]
        serializer = _serializer({"services": services})
        assert not serializer.is_valid()

    def test_deploy_targets_over_50_items_rejected(self):
        targets = [{"name": f"d{i}"} for i in range(51)]
        serializer = _serializer({"deploy_targets": targets})
        assert not serializer.is_valid()

    def test_notes_over_10000_chars_rejected(self):
        serializer = _serializer({"notes": "x" * 10_001})
        assert not serializer.is_valid()

    def test_notes_exactly_10000_chars_accepted(self):
        serializer = _serializer({"notes": "x" * 10_000})
        assert serializer.is_valid(), serializer.errors
