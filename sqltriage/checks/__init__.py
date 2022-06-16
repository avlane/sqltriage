"""The individual checks.

A check module defines ``NAME`` and ``run(ctx)``, which returns a list of
``Finding`` objects.  Register new modules in ``CHECKS`` below.
"""
from . import blocking, vlf, waits

CHECKS = {module.NAME: module for module in (waits, blocking, vlf)}
