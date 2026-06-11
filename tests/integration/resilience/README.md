# tests/integration/resilience — kit ↔ Redis integration

Covers how the boilerplate wires `resilience_kit` against real Redis:
the FastAPI middleware install order, the throttle dependency, the
security-headers middleware, and request-id propagation under the live
lifespan.

These tests build their own `TestClient(app)` with the lifespan
engaged. The integration tier rule "don't import from another tier's
conftest" is preserved — the `live_client` fixture is duplicated
inline rather than imported from `tests/e2e/conftest.py`.
