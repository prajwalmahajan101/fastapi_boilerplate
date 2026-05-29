# src/common — settings, enums, OpenAPI metadata

> Thin starter notes. Add concrete settings/enums documentation as the
> project's configuration surface grows.

## What lives here

- `settings.py` — `Settings(CoreSettings)`. Infra knobs are inherited from
  `core.settings.CoreSettings`; **add your application's fields here**.
  The process-wide `settings` singleton is exported from this module.
- `enums.py` — domain `StrEnum`s + convenience re-exports of core enums.
- `constants.py` — shared magic strings / namespaces.
- `openapi_metadata.py` — the long-form API description, tag docs, and the
  shared `RESPONSES_*` / `DEFAULT_RESPONSES` dicts used by routes.

## Dependency rule

`src.common` **may** import from `src.core`. `src.core` must **never**
import from `src.common` — core reads config via `core.runtime`, into which
`app.py` injects the concrete `settings` at startup. The reverse direction
is mechanically enforced by `scripts/check_layering.py` (pre-commit).

## Common pitfalls

- **Defining a new setting only in `Settings` without updating
  `docs/environment.md`** — `scripts/dump_settings_schema.py --check`
  fails CI. Run `python scripts/dump_settings_schema.py --write` to
  regenerate the matrix before committing.
- **Importing settings inside `src.core`** — use
  `core.runtime.get_settings()`; the runtime indirection is what
  preserves the one-rule layering invariant.
- **A new enum that duplicates a core enum** — check `src.core.enums`
  first; the convenience re-exports in `enums.py` are explicit.
- **Hand-editing the auto block in `docs/environment.md`** — won't
  survive the next `--write`. Put hand-written prose around the markers.

## Reference examples

- Subclassing `CoreSettings`: `src/common/settings.py`.
- OpenAPI response dict definition + composition: `src/common/openapi_metadata.py`.
