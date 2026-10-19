"""
DEPRECATED: This module has been renamed to 'esdglider.metadata'.

This shim is maintained for backward compatibility and will be removed in a
future release.
"""

import warnings

# Issue a warning whenever someone imports this module
warnings.warn(
    "The 'esdglider.config' module is deprecated and has been renamed to "
    "'esdglider.metadata'. Please update your imports, as 'config' will be "
    "removed in a future version.",
    category=DeprecationWarning,
    stacklevel=2,
)

# Re-export all public functions, classes, and variables from metadata.py
from esdglider.metadata import *  # noqa: F401, F403