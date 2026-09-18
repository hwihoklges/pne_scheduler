# Frontend dependency maintenance

The September 2026 security update pins Next.js to **15.5.25**, retaining the
existing Next 15 architecture. Exact npm overrides pin **PostCSS 8.5.23** and
**Sharp 0.35.4** to eliminate the remaining high-severity advisories in the
maintenance candidate (PostCSS <=8.5.22 and Sharp <0.35.4). The overrides apply
to these two transitive package names only. Next's declared PostCSS dependency
still says 8.4.31; npm's override and the resolved lock entry intentionally differ.

These are temporary security overrides, not a general upgrade policy. Review
them whenever Next is updated; remove each only when the upstream resolution
is patched and the full audit and compatibility checks pass. A Next 16 migration
requires a separate planned compatibility review; do not use `npm audit fix
--force` as an automatic major-version migration.

Use Node 22 as in CI (Sharp requires Node >=20.9). The legacy `lint` command is
now a TypeScript check, not ESLint; `typecheck` exposes the same `tsc --noEmit`
check explicitly. Existing controller tests and loopback host bindings remain.

## Lock generation and verification

The manual `dependency-lock-candidate` job in
[ci.yml](../.github/workflows/ci.yml) resolves the explicitly pinned maintenance
versions and produces an artifact containing the manifest, lockfile and audit.
It has read-only repository permissions and never commits changes. Update its
pins deliberately for future maintenance; review the artifact before applying.
Generation uses `--package-lock-only --ignore-scripts`, so generation alone is
not installation or runtime evidence.

Candidate run [35302032370](https://github.com/hwihoklges/pne_scheduler/actions/runs/35302032370)
verified registry versions and reported zero vulnerabilities at every severity.
The committed lock must match that candidate's package metadata and integrity
hashes. PR CI then runs `npm ci`, blocking `npm audit --audit-level=high`, controller
tests, production build, typecheck, native Sharp image processing, and Chromium
UI/Flask-proxy smoke, alongside the three Python matrix jobs. Inspect the latest
head's results before merging. Audit results are time-specific, not a guarantee
against future disclosures. Browser-test tooling is installed separately without
changing the application lockfile.