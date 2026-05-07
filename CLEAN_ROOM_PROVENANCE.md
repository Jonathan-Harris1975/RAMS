# Clean-Room Provenance

## Repo Management Suite · v1.0 · May 2026

This document certifies the provenance of every source file in this repository.

### Build Method

All code in `repo_mgmt/` and `tests/` was derived **exclusively** from the functional
specification contained in:

> *Repo Management Suite · Build Prompt · v1.0 · May 2026*

No upstream source code, prompts, parser logic, CLI strings, internal class names,
or package namespaces from any third-party autonomous-coding tool were copied,
adapted, or referenced during implementation.

### What "Clean-Room" Means Here

| Constraint | Status |
|---|---|
| No Aider source files read or copied | ✓ Confirmed |
| No Aider class names used | ✓ Confirmed |
| No Aider CLI strings reproduced | ✓ Confirmed |
| No Aider internal prompts embedded | ✓ Confirmed |
| No Aider package namespace (`aider.*`) referenced | ✓ Confirmed |
| Implementation derived solely from the RMS specification | ✓ Confirmed |

### Third-Party Libraries

All third-party libraries listed in `pyproject.toml` are used as public API only
(imports of their documented public interfaces). See `THIRD_PARTY_NOTICES.md` for
full attribution.

### Specification Reference

- Document: *Repo Management Suite · Build Prompt · v1.0 · May 2026*
- Sections: §1 – §15
- Build date: 2026-05-05
