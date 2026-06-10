"""Enforce the ``BaseCustomError`` status-map registration ordering contract.

``src/core/exceptions/handlers.py`` documents that mappings are checked
with ``isinstance`` in registration order — specific subclasses must be
registered before their parents, otherwise the parent would shadow the
child. The contract is enforced by code review today; this test makes it
executable so a regression fails CI instead of a release.
"""

from __future__ import annotations

from src.core.base.exception import BaseCustomError
from src.core.exceptions import handlers as _handlers_module
from src.core.exceptions.handlers import _get_status_map  # type: ignore[attr-defined]


def _force_register_all_modules() -> None:
    """Import every project module that defines ``BaseCustomError`` subclasses.

    ``__subclasses__`` only reports classes already loaded into the
    process. Importing the modules below makes the test deterministic
    regardless of which test pulled them in first.
    """
    import src.core.exceptions  # noqa: F401
    import src.core.exceptions.api  # noqa: F401
    import src.core.exceptions.infrastructure  # noqa: F401
    import src.core.exceptions.rate_limit  # noqa: F401
    import src.core.exceptions.repository  # noqa: F401
    import src.core.exceptions.validation  # noqa: F401


def test_registered_subclasses_precede_their_parents() -> None:
    """Every registered class appears before any registered ancestor."""
    _force_register_all_modules()
    status_map = _get_status_map()
    positions: dict[type, int] = {cls: idx for idx, (cls, _) in enumerate(status_map)}

    violations: list[str] = []
    for cls, idx in positions.items():
        for ancestor in cls.__mro__[1:]:
            if ancestor is cls or ancestor not in positions:
                continue
            if positions[ancestor] < idx:
                violations.append(
                    f"{cls.__name__} (#{idx}) is registered after its ancestor "
                    f"{ancestor.__name__} (#{positions[ancestor]}); "
                    "the ancestor entry would shadow the child."
                )

    assert not violations, "Status-map ordering violations:\n" + "\n".join(violations)


def test_status_map_only_contains_basecustomerror_subclasses() -> None:
    """No accidental non-``BaseCustomError`` entries slip into the map."""
    for cls, _ in _get_status_map():
        assert issubclass(cls, BaseCustomError), (
            f"{cls.__name__} is registered but does not derive from BaseCustomError."
        )


def test_status_map_is_non_empty_after_module_import() -> None:
    """Smoke test — the handlers module always registers at least one mapping."""
    assert _handlers_module._get_status_map(), (
        "Status map is empty; handlers.py registration block did not run."
    )
