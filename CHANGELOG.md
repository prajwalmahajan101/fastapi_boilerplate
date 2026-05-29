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
</content>
</invoke>