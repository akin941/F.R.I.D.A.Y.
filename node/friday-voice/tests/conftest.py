import importlib.util
import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]
SOURCE_DIR = PACKAGE_ROOT / "friday_voice"


def load_script(name):
    """Import one of scripts/*.py by path (they are not a package)."""
    path = PACKAGE_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"friday_voice_scripts_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo_root():
    return REPO_ROOT
