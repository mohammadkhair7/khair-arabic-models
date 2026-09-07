"""Silver-label teachers.

Only needed to *regenerate* a training set from raw text. Training and
inference with the shipped models never import anything from here.
"""
from .camel_morphology import CamelMorphologyEngine

__all__ = ["CamelMorphologyEngine"]
