"""Package layout sanity checks."""

import importlib
import pkgutil
import sys

import maf_test_harness


def test_every_module_in_the_package_imports():
    names = [m.name for m in pkgutil.walk_packages(maf_test_harness.__path__, "maf_test_harness.")]
    assert len(names) > 60
    for name in names:
        importlib.import_module(name)
        assert name in sys.modules


def test_skills_root_still_points_at_the_repository_skills_directory():
    from maf_test_harness.monitoring import report

    assert (report.DEFAULT_SKILLS_ROOT / "SKILLS.lock.json").is_file()
