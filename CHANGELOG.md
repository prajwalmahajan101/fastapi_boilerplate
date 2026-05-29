# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project does not yet publish releases, so changes are grouped under
`Unreleased` until a tagged release exists.

## Unreleased

### Added

- Typed success envelopes on every v1 route: `response_model=SuccessEnvelope[…]`
  on `items` (Create/List/Get/Update/Delete) and `hello`, so Swagger renders
  the concrete `data` shape instead of a free-form object.
- `scripts/check_openapi_metadata.py` now also flags routes missing
  `response_model=`, keeping the contract enforced at CI time. (ISSUE-017)

### Changed

- **Breaking (internal):** `BaseRepository.list` / `list_paginated` / `count`
  and `BaseService.list` / `list_paginated` now default `active_only=True`.
  Soft-deleted rows are no longer returned unless callers opt in via
  `active_only=False`. The example `list_items` route drops its now-redundant
  explicit flag. (ISSUE-018)
</content>
</invoke>