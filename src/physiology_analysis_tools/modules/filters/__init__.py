from ..registry import Registry, discover

FILTERS = Registry("filters")

discover(__name__, __path__)