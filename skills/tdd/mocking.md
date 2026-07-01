# When to Mock

Mock only at **system boundaries** — external APIs, databases, time/randomness, and sometimes the file system.

Avoid mocking things you control: your own classes/modules, internal collaborators.

## Key Design Principles

**Dependency Injection** makes boundaries testable by passing dependencies in rather than instantiating them internally.

**SDK-style interfaces** are preferred over generic fetchers. Each mock returns one specific shape — no conditional logic in test setup — and it's easy to see which endpoints a test exercises.

A generic `api.fetch(endpoint, options)` approach forces conditional logic into mocks, while discrete functions like `getUser`, `getOrders`, and `createOrder` remain independently testable.
