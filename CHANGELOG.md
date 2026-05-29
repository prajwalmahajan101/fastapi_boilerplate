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

### Fixed

- `capture_and_dispatch` and `persist_log` now log audit-pipeline failures
  with `extra={"service_name", "direction", "request_id", "log_id"}`, so a
  build- or save-side regression is correlatable to the originating call
  from logs alone. `capture_and_dispatch` takes optional `service_name=` and
  `direction=` kwargs for the build-fail correlation; inbound / outbound
  decorators pass them. (ISSUE-021)

### Performance

- `PostgresApiLogRepository` now batches audit writes behind an internal
  queue: a single background drain task accumulates up to
  `api_log_batch_size` rows (or up to `api_log_batch_max_interval_seconds`
  of idle) and flushes them as one bulk `INSERT ... ON CONFLICT DO NOTHING`.
  The audit subsystem no longer pays a per-row `engine.begin()` round-trip
  on the shared pool, so it stops competing with request-path queries under
  burst load. New settings: `api_log_batch_size` (100),
  `api_log_batch_max_interval_seconds` (1.0s),
  `api_log_batch_queue_size` (5000). (ISSUE-019)
</content>
</invoke>