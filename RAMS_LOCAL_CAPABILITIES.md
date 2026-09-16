# RAMS repository-local capabilities

RAMS does not depend on a shared skills bucket, a public descriptor URL, AI Search, Skills.sh or a runtime installer.

The authoritative capability records are the `RAMS-skNNN-*.local.json` files under `config/skills/`. Each record describes behaviour already implemented by RAMS and names the repository paths that provide it. `repo_mgmt/local_skills.py` validates identifiers, provenance and implementation paths before the records are included in a report.

## Current records

| ID | Capability | Native implementation |
|---|---|---|
| `RAMS-sk001` | Audit evidence normalisation | Audit reader, issue normaliser and search-visibility baseline |
| `RAMS-sk002` | Controlled remediation | Task ranking, patch planning, patch protocol and patch application |
| `RAMS-sk003` | Verification and release gating | Validation, automation gates, update execution and pull-request handoff |
| `RAMS-sk004` | Repository safety and rollback | Git safety, snapshots and runtime cleanup |

Capability records are metadata, not executable instructions. They cannot grant permission to patch, commit, push, open a pull request or deploy. Existing RAMS approval, protected-path and validation controls remain authoritative.

## Updating the catalogue

Add or change a record in the same change as its native implementation and tests. Use the `RAMS-skNNN` prefix, keep descriptions original to this repository, set `external_skill_content_copied` to `false`, and run the normal compile, pytest, Ruff and mypy checks before release.
