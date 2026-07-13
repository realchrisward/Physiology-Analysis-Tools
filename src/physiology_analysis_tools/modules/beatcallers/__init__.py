from ..registry import Registry, discover

FILTERS = Registry("beatcallers")

discover(__name__, __path__)