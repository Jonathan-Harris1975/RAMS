# Autonomous repair request

- Failed workflow: Security and repository quality
- Failed commit: 6420ce6508f9e1bf9e6de28e4dec6d0da78af8c2
- Failed run: https://github.com/Jonathan-Harris1975/RAMS/actions/runs/36815091498
- Run ID: 36815091498
- Security-classified workflow: true

Fix the smallest code, dependency-manifest, lockfile, build or deployment-configuration defect that caused this failure.
Delete this file only after the underlying defect is fixed and the relevant repository checks pass.

Guardrails:
- Do not weaken tests, CodeQL, Trivy, Gitleaks, required checks, branch/ruleset protections, workflow permissions, secret handling or security policy.
- Do not dismiss CodeQL alerts, reduce query coverage, add broad suppressions or change secret allowlists merely to make CI green.
- Do not merge or deploy directly.
- If credentials, destructive data changes, platform administration, alert dismissal or a security-policy decision are required, keep this marker and use autonomy:human-hold.
