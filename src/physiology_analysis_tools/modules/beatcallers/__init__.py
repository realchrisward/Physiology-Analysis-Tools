# -*- coding: utf-8 -*-
"""
Pluggable beat callers.

Add one by dropping a module in this package that defines a class decorated
with @BEATCALLERS.register. Nothing else needs to change - not this file, not
main.py, not the UI.

The registry itself lives in modules.registries, so the order of the lines
below cannot break anything.
"""

__version__ = "0.0.2"

from ..registries import BEATCALLERS  # re-exported for convenience
from ..registry import discover

discover(__name__, __path__)

__all__ = ["BEATCALLERS"]
