# FastAPI Boilerplate — dev convenience targets.
#
# All targets are idempotent and safe to run repeatedly. Targets that
# mutate anything (regenerating requirements, writing SBOM, etc.) are
# explicitly named with an action verb.

.PHONY: help audit audit-all check deps-check sbom sbom-diff install-hooks \
	test test-unit test-integration test-e2e \
	test-cov test-cov-html test-cov-open coverage-clean \
	audit-hot-path settings-schema settings-schema-check stale-refs

# Pytest passthrough: `make test ARGS="-k foo -x"`
ARGS ?=
PYTEST := pytest

# Single source of truth for the Python base image used by the audit
# / sbom containers. Keep aligned with Dockerfile's FROM line. To pin
# to a digest (reproducible audits + SBOMs), run:
#   docker pull python:3.12-slim && \
#   docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim
# and replace the tag below with the resulting `python:3.12-slim@sha256:...`.
PYTHON_IMAGE := python:3.12-slim

# Pinned tooling — scanners that silently upgrade themselves produce
# non-reproducible findings. Refresh alongside the quarterly dep audit.
PIP_AUDIT_VERSION  := 2.7.3
CYCLONEDX_VERSION  := 7.3.0

# Ephemeral audit container needs the build-time system packages that
# the Dockerfile installs to compile asyncpg / psycopg-style native
# wheels. Pin via `docker run --rm $(PYTHON_IMAGE) apt-cache policy`.
AUDIT_SYSTEM_DEPS := apt-get update -qq && apt-get install -y -qq --no-install-recommends \
	gcc \
	libc6-dev \
	libpq-dev

help:  ## Show available targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-22s %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Code-quality / drift guards
# ---------------------------------------------------------------------------

audit-hot-path:  ## Profile capture_and_dispatch overhead (p50/p95/p99)
	python scripts/profile_audit_path.py

settings-schema:  ## Print the env-vars schema dumped from CoreSettings
	python scripts/dump_settings_schema.py

settings-schema-check:  ## Fail if docs/environment.md drifts from Settings
	python scripts/dump_settings_schema.py --check

stale-refs:  ## Scan docs + source for known-stale references
	python scripts/check_stale_refs.py

# ---------------------------------------------------------------------------
# Dependency security
# ---------------------------------------------------------------------------

audit:  ## Run pip-audit against requirements/base.lock.txt (hash-pinned)
	docker run --rm -v "$(CURDIR)/requirements:/reqs:ro" $(PYTHON_IMAGE) \
		sh -c "$(AUDIT_SYSTEM_DEPS) && pip install --quiet pip-audit==$(PIP_AUDIT_VERSION) && pip-audit -r /reqs/base.lock.txt --require-hashes"

audit-all:  ## Run pip-audit against every layered requirements file
	@for layer in base dev; do \
		echo; \
		echo "=== pip-audit: requirements/$$layer.txt ==="; \
		docker run --rm -v "$(CURDIR)/requirements:/reqs:ro" $(PYTHON_IMAGE) \
			sh -c "$(AUDIT_SYSTEM_DEPS) && pip install --quiet pip-audit==$(PIP_AUDIT_VERSION) && pip-audit -r /reqs/$$layer.txt" \
			|| echo "!! audit failed for $$layer.txt"; \
	done

check:  ## Run `pip check` against base deps (detects transitive version conflicts)
	docker run --rm -v "$(CURDIR)/requirements:/reqs:ro" $(PYTHON_IMAGE) \
		sh -c "$(AUDIT_SYSTEM_DEPS) && pip install --quiet --require-hashes -r /reqs/base.lock.txt && pip check"

deps-check:  ## Verify each requirements/*.txt is in sync with its .in (fails on drift)
	@set -e; \
	for layer in base dev; do \
		echo "=== deps-check: requirements/$$layer.{in,txt} ==="; \
		if [ "$$layer" = "dev" ]; then extra_flags="--allow-unsafe"; else extra_flags=""; fi; \
		docker run --rm -v "$(CURDIR):/repo:ro" -w /repo $(PYTHON_IMAGE) \
			sh -c "$(AUDIT_SYSTEM_DEPS) && pip install --quiet pip-tools && \
				pip-compile --quiet $$extra_flags \
					--output-file=/tmp/$$layer.txt requirements/$$layer.in && \
				diff -u requirements/$$layer.txt /tmp/$$layer.txt > /dev/null \
					|| { echo '!! drift: requirements/$$layer.txt is out of sync with $$layer.in — run pip-compile'; exit 1; }"; \
	done; \
	echo "all layers in sync."

sbom:  ## Generate CycloneDX SBOM for base deps at sbom/base-sbom.json
	@mkdir -p sbom
	@host_uid=$$(id -u); host_gid=$$(id -g); \
	docker run --rm \
		-v "$(CURDIR)/requirements:/reqs:ro" \
		-v "$(CURDIR)/sbom:/out" \
		$(PYTHON_IMAGE) \
		sh -c "pip install --quiet cyclonedx-bom==$(CYCLONEDX_VERSION) && \
			cyclonedx-py requirements --output-file /out/base-sbom.json /reqs/base.txt && \
			chown $${host_uid}:$${host_gid} /out/base-sbom.json"
	@echo "wrote sbom/base-sbom.json"

sbom-diff:  ## Diff a freshly-generated SBOM against the committed sbom/base-sbom.json
	@if [ ! -f sbom/base-sbom.json ]; then \
		echo "!! sbom/base-sbom.json missing — run 'make sbom' to create the baseline first."; \
		exit 1; \
	fi
	@tmpdir=$$(mktemp -d); \
	host_uid=$$(id -u); host_gid=$$(id -g); \
	docker run --rm \
		-v "$(CURDIR)/requirements:/reqs:ro" \
		-v "$$tmpdir:/out" \
		$(PYTHON_IMAGE) \
		sh -c "pip install --quiet cyclonedx-bom==$(CYCLONEDX_VERSION) && \
			cyclonedx-py requirements --output-file /out/base-sbom.json /reqs/base.txt && \
			chown $${host_uid}:$${host_gid} /out/base-sbom.json" >/dev/null; \
	if diff -u \
		<(jq -S '.components | map({name, version, purl}) | sort_by(.purl)' sbom/base-sbom.json) \
		<(jq -S '.components | map({name, version, purl}) | sort_by(.purl)' "$$tmpdir/base-sbom.json"); then \
		echo "sbom in sync."; \
	else \
		echo; \
		echo "!! sbom drift — components above differ from sbom/base-sbom.json."; \
		echo "   If intentional, run 'make sbom' and commit the updated baseline."; \
		rm -rf "$$tmpdir"; \
		exit 1; \
	fi; \
	rm -rf "$$tmpdir"

# ---------------------------------------------------------------------------
# Test targets
# ---------------------------------------------------------------------------

test:  ## Run the default test suite (unit + integration + e2e)
	$(PYTEST) $(ARGS)

test-unit:  ## Run only unit-layer tests (fast, no I/O)
	$(PYTEST) -m unit $(ARGS)

test-integration:  ## Run only integration-layer tests (DB + Redis)
	$(PYTEST) -m integration $(ARGS)

test-e2e:  ## Run only e2e-layer tests (full HTTP round-trip)
	$(PYTEST) -m e2e $(ARGS)

test-cov:  ## Run tests with coverage; terminal report + coverage.xml
	$(PYTEST) --cov --cov-report=term-missing --cov-report=xml $(ARGS)

test-cov-html:  ## Same as test-cov plus htmlcov/ directory
	$(PYTEST) --cov --cov-report=term-missing --cov-report=xml --cov-report=html $(ARGS)

test-cov-open: test-cov-html  ## Generate HTML report and open it in the default browser
	@if command -v xdg-open >/dev/null 2>&1; then xdg-open htmlcov/index.html; \
	elif command -v open >/dev/null 2>&1; then open htmlcov/index.html; \
	else echo "open htmlcov/index.html manually"; fi

coverage-clean:  ## Remove coverage artifacts
	@rm -rf .coverage .coverage.* coverage.xml htmlcov/
	@echo "removed coverage artifacts."

# ---------------------------------------------------------------------------
# Git hooks
# ---------------------------------------------------------------------------

install-hooks:  ## Install repo git hooks into .git/hooks (symlink, idempotent)
	@if [ ! -d .git ]; then echo "!! not a git repo — nothing to install"; exit 1; fi
	@if [ ! -d scripts/git-hooks ]; then \
		echo "no scripts/git-hooks/ directory — nothing to install"; \
		exit 0; \
	fi
	@for hook in scripts/git-hooks/*; do \
		name=$$(basename $$hook); \
		ln -sf "../../$$hook" ".git/hooks/$$name"; \
		echo "installed .git/hooks/$$name -> $$hook"; \
	done
