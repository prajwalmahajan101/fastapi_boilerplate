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
`app.py` injects the concrete `settings` at startup.
