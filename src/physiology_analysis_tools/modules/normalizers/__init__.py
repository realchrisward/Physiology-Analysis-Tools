from ..registry import Registry, discover

FILTERS = Registry("normalizers")

discover(__name__, __path__)