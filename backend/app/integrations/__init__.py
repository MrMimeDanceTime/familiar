"""Third-party deck platform integrations (Archidekt, Moxfield, ...).

Each provider declares which directions it supports: fetching a deck FROM the
platform into Familiar, pushing a deck TO the platform, or both. Providers are
registered by name; the API layer dispatches on the provider name and the
capability flags. See base.py for the interface and registry.
"""
