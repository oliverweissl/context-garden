"""Core infrastructure shared by all Context Garden Skills.

Modules:
    artifacts  -- content-addressed storage for large outputs
    events     -- the common context-activity event schema
    provenance -- linking generated facts/summaries back to their sources
    store      -- project-local persistence (SQLite-backed)
    tokens     -- token estimation and cost accounting
"""
