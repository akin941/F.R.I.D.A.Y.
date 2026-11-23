"""Shared fixtures for the `ops` tests.

The Phase 8B closeout's `graphify` check reads the pinned image's labels
through the `docker_image_labels` seam. No test may reach a Docker daemon
(brief "Test strategy"), so every `ops` test starts with a seam that
behaves like a host without a `docker` binary; a test that exercises the
image branch installs its own fake."""

import pytest

from ops import preflight


def _no_docker(image, *, timeout):
    raise FileNotFoundError("docker")


@pytest.fixture(autouse=True)
def no_docker_daemon(monkeypatch):
    monkeypatch.setattr(preflight, "docker_image_labels", _no_docker)
