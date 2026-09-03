# Dev container

A reproducible development environment for TinLantern. Open the repo in a
devcontainer-aware editor (or GitHub Codespaces) and it builds
automatically.

## What you get

- **Python 3.12** — the project runtime
- **Docker-in-Docker** — so `docker compose` (Postgres, and later the full
  local stack) runs inside the container
- **Node LTS** — for the React/Vite frontend (M5)
- **GitHub CLI (`gh`)** — for PR and release workflows
- **zsh**, passwordless `sudo`, a `C.UTF-8` locale

## First steps

```sh
make setup   # install deps, bring up Postgres 16, run migrations
make test    # run the suite
```

`make setup` waits for the database healthcheck and then runs
`alembic upgrade head`, creating the `raw` and `warehouse` schemas.

`postCreateCommand` already runs `pip install -e ".[dev]"` and marks
`/workspace` as a git safe directory, so the test harness is usable as
soon as the container is up.

Personal tooling and editor config are layered in separately and are not
part of this repository.
