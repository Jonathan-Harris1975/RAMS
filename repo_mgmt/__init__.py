"""
Repo Management Suite (RMS) — clean-room autonomous repository management service.

Exposes an HTTP API that triggers independent, self-contained audit pipelines.
Each pipeline reads an R2 audit snapshot, normalises issues, plans and applies
patches, runs validation, and publishes a run report back to R2.
"""

__version__ = "1.1.0"
