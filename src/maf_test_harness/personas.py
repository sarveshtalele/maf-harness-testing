"""Compatibility shim: this module moved to ``maf_test_harness.agent.personas``; the old name is the same module object."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("maf_test_harness.agent.personas")
