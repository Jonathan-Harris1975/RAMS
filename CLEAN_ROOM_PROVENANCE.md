# Clean-Room Provenance

## Repository Automation Management Service · v1.0 · May 2026

This document records the provenance boundary for every source file in this repository.

## Build method

All code in `repo_mgmt/` and `tests/` is derived from the supplied RMS functional specification and the RAMS production-readiness audit findings.

No upstream source code, prompts, parser logic, CLI strings, internal class names, or package namespaces from third-party autonomous-coding tools are part of this deliverable.

## Excluded reference material

A separate repository archive was supplied as reference-only material with provenance risk. It is not copied into RAMS, not used as implementation source, not included in the Docker context, not referenced by CI, and not included in release artefacts.

The release artefact contains `RAMS-main` only.

## Verification notes

| Constraint | Status |
|---|---|
| Source derived from RMS specification and readiness findings | Confirmed |
| Reference archive excluded from source and Docker context | Confirmed |
| No third-party autonomous-coding package namespace in runtime source | Confirmed |
| CI operates on RAMS files only | Confirmed |

## Third-party libraries

All third-party libraries listed in `pyproject.toml` are used through their documented public APIs. See `THIRD_PARTY_NOTICES.md` for attribution.
