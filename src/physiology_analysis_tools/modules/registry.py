# -*- coding: utf-8 -*-
"""
Generic plugin registry for Physiology Analysis Tools.

Used by modules.filters and modules.beatcallers so that adding a new
algorithm is a matter of dropping a single file into the relevant package -
no edits to main.py or to any __init__.py are required.

written for Physiology Analysis Tools (C) 2024
"""

__version__ = "0.0.1"

import importlib
import pkgutil


class Registry:
    """
    Name -> class registry.

    Registration is idempotent (re-registering the same ``name`` overwrites the
    old entry) so that ``importlib.reload`` in DEVMODE does not create
    duplicates or leave stale classes behind.
    """

    def __init__(self, kind="algorithm"):
        self.kind = kind
        self._items = {}

    def register(self, cls):
        """Class decorator. Requires ``cls.name``."""
        name = getattr(cls, "name", None)
        if not name:
            raise ValueError(
                f"cannot register {cls!r} as a {self.kind} - missing 'name'"
            )
        self._items[name] = cls
        return cls

    # -- lookup ------------------------------------------------------------
    def __getitem__(self, name):
        return self._items[name]

    def get(self, name, default=None):
        return self._items.get(name, default)

    def names(self):
        return list(self._items)

    def labels(self):
        """{name: human readable label} - convenient for populating combos."""
        return {n: getattr(c, "label", n) for n, c in self._items.items()}

    def items(self):
        return self._items.items()

    def __contains__(self, name):
        return name in self._items

    def __iter__(self):
        return iter(self._items.values())

    def __len__(self):
        return len(self._items)


def discover(package_name, package_path):
    """
    Import every non-private submodule of a package so that decorated classes
    register themselves. Call at the bottom of the package __init__.py::

        discover(__name__, __path__)

    Import failures are reported but never fatal - a filter that depends on an
    optional library should not prevent the rest of the tool from starting.
    """
    failed = {}
    for _, mod_name, _ in pkgutil.iter_modules(package_path):
        if mod_name.startswith("_"):
            continue
        try:
            importlib.import_module(f".{mod_name}", package=package_name)
        except Exception as e:  # noqa: BLE001 - deliberately broad
            failed[mod_name] = e
            print(f"unable to load {package_name}.{mod_name}: {e}")
    return failed
