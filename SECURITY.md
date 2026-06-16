# RAMS security policy

**Status:** Production-controlled  
**Last reviewed:** 16 June 2026

RAMS can inspect and alter repositories, so production access is deliberately narrow. Protected endpoints require `RMS_API_KEY`; live writes require explicit enablement in addition to dry-run, release and validation gates. Repository paths, protected files, branch naming and validation commands are constrained by code and configuration.

R2, GitHub and OpenRouter credentials belong in Koyeb Secrets. The API returns no raw credentials and uses restrictive response headers. Run as one non-root worker unless concurrency has been re-evaluated. Report suspected unauthorised writes, token exposure or validation bypass privately to the repository owner.
