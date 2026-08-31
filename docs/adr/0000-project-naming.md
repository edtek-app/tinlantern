# ADR-0000: Project naming — Beacon → TinLantern
Status: Accepted

## Context
Working name "Beacon" failed a pre-commit collision check: Anthology
(formerly Campus Labs) ships "Beacon," a widely deployed early-alert
system — the same product category and customer base as this project.
Secondary strikes: "web beacon" is tracking-pixel terminology (poor fit
for a product already exposed to surveillance critiques) and the generic
word is unsearchable/unownable (GitHub, PyPI, domains).

## Decision
Rename to **TinLantern** (one word). xAPI — this project's native
protocol — originated as Rustici Software's "Project Tin Can," and the
Tin Can name persists in the community. A tin lantern is a punched can
that gives light: light made from tin cans is literally this product.
We deliberately avoid the verbatim "Tin Can API" compound, which was
trademarked during the original ADL project. Vetting: exact-match,
registry, and edtech-collision searches all clean (only collisions are a
California restaurant and craft tutorials — unrelated classes).

## Consequences
Brand couples to xAPI; acceptable because xAPI-native IS the v1
positioning. If ingestion later broadens (Caliper, SIS), revisit naming.
README/SEO always lead with "xAPI" (the standard's proper name).
