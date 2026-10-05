"""Comit integration engine package.

Owns outbound connectors to external data sources and inbound mock registries.
The real FSP registry API is intentionally not provided at the hackathon (TZ §4):
we model the contract ourselves here, and the most convincing shapes may be adopted
by FSP when they build the real integration (context/tz/00-fsp-tz-2026.md §2).
"""
