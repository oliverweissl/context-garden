"""Context Garden shared runtime.

This package provides the infrastructure shared by Context Gardening Skills:
artifact storage, provenance tracking, event recording, token accounting,
and project-local state. It intentionally implements infrastructure only —
Skill behavior lives under ``skills/``, not here.
"""

__version__ = "0.1.0"
