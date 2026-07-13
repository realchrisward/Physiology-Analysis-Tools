# -*- coding: utf-8 -*-
"""
The three plugin registries, defined in one import-safe place.

Why this module exists: if a registry is defined inside its own package's
__init__.py, then that __init__ has to define the registry BEFORE it calls
discover() - because discovery imports submodules that ask the half-initialised
package for the registry. That makes the file order-sensitive, and getting it
wrong produces a baffling "partially initialized module ... circular import"
error. Defining the registries here removes the hazard entirely: the packages
just re-export them and run discovery, and the order cannot matter.

Algorithm modules import from HERE:

    from ..registries import BEATCALLERS

written for Physiology Analysis Tools (C) 2024
"""

__version__ = "0.0.1"

from .registry import Registry

FILTERS = Registry("filter")
NORMALIZERS = Registry("normalizer")
BEATCALLERS = Registry("beatcaller")
