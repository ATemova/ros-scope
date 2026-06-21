# Contributing

Thanks for taking a look. This project is a portfolio piece but PRs and issues
are welcome.

## Dev setup

The full stack runs in Docker:

```bash
docker compose up --build      # http://localhost:8000
```

The rule-engine, schema, and simulator tests run without any containers:

```bash
pip install -r requirements-dev.txt
ruff check .
pytest -q
```

## Conventions

- Keep the pure logic (rule evaluation, schema, simulation) free of I/O so it
  stays unit-testable without Redis or Postgres.
- Run `ruff check . --fix` before committing.
- Producers (the synthetic publisher and the ROS 2 bridge) must emit the shared
  envelope in `common/schema.py` — never write to storage directly.
