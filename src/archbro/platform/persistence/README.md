# Persistence

Archbro backend code depends on `ProjectRepositoryPort`; concrete storage implementations belong here.

- `postgres.py` — the PostgreSQL implementation, and the only one.

One implementation is deliberate. Keeping several backends behaviourally
identical is a standing source of bugs -- the kind where one store raises a
different exception than another on the same input, which no reasonable test
catches until production does. `ARCHBRO_PERSISTENCE` accepts only `postgres`,
and `DATABASE_URL` is required.

Jim's agent/API code never imports this concrete adapter.
