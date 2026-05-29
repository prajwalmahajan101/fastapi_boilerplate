# Dependency management

The boilerplate uses `pip-compile` (pip-tools) to pin runtime + dev
dependencies. Three files per environment:

```
requirements/
  base.in        # human-edited top-level production deps
  base.txt       # pip-compiled — pinned versions, no hashes
  base.lock.txt  # pip-compiled --generate-hashes — supply-chain lock
  dev.in         # extra dev tools (pytest, ruff, …)
  dev.txt
```

## Editing

1. Edit `base.in` (or `dev.in`) — top-level requirements only.
2. Regenerate both lockfiles:

   ```bash
   pip-compile --output-file=requirements/base.txt requirements/base.in
   pip-compile --generate-hashes --output-file=requirements/base.lock.txt requirements/base.in
   ```

3. Commit `*.in` + both `*.txt` + `*.lock.txt` in the same atomic commit.

The `pre-commit` config runs `pip-compile --dry-run` as a check —
the hook fails if the `.in` and `.txt` drift.

## Installing

Local dev:

```bash
pip install -r requirements/dev.txt
```

Production container:

```bash
pip install --require-hashes -r requirements/base.lock.txt
```

The `--require-hashes` flag means any tampering / mirror-substitution
fails the install.

## Auditing

```bash
make audit
```

Runs `pip-audit` against the lockfile; failing CVEs surface as a
non-zero exit. Pin the dep in `base.in`, regenerate, commit.

## Optional dependencies

Several runtime deps are imported lazily — deployments that disable
the corresponding feature can drop them from their *own* requirements
without changing the boilerplate. The boilerplate pins them so the
defaults work:

| Package | Pulled in by |
|---|---|
| `PyJWT` | `src.auth.jwt` (when `"jwt"` enabled). |
| `authlib`, `itsdangerous` | `src.auth.oauth_google`. |
| `pybreaker` | `circuit_breaker_backend="pybreaker"`. |
| `boto3` | AWS Secrets Manager + S3 + SES helpers. |

## Removing a dep

Delete the line in `base.in`, regenerate `*.txt`, re-run tests, run
`make audit`. The dead-utils check
(`scripts/check_dead_utils.py`) catches the case where a helper
that depended on the removed package still has a public import
surface.
