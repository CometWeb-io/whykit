# Releasing WhyKit

A numbered checklist for the maintainer who cuts a package release. Each step
names the evidence to look at before moving on. Nothing here runs by itself:
the release workflow starts only when a `v*` tag is pushed, and pushing that
tag is the maintainer's decision alone.

The repository-visibility checklist lives in the
[release checklist](../.github/RELEASE.md); this page covers the package.

## What the pipeline already guarantees

Every pull request and every push to `main` runs the `package` job in
[`ci.yml`](../.github/workflows/ci.yml). It:

- builds the wheel and sdist **twice** with `SOURCE_DATE_EPOCH` set to the
  commit time and fails unless both builds are byte-identical;
- runs [`scripts/check_dist.py`](../scripts/check_dist.py), which checks the
  version, `py.typed`, every vault-template file, PEP 639 licence metadata,
  zero runtime dependencies, PyPI-safe README links and the sdist allowlist;
- runs `twine check --strict`;
- installs the wheel into a clean environment on the **lowest supported
  Python** (3.11) and the sdist on 3.12, then runs `init`, `lint` and `doctor`;
- generates a CycloneDX SBOM of the clean wheel install, checks that it lists
  WhyKit at the built version and nothing else, and keeps the archives and
  SBOM as the `distributions` workflow artifact for 14 days.

The [release workflow](../.github/workflows/release.yml) reruns all CI gates on
the tagged commit, then a `build` job with no publishing credential repeats the
reproducible build, the checks and the SBOM, and uploads them. Only the
`publish` job, gated by the `pypi` environment, holds the OIDC token. It
downloads the checked archives, never rebuilds them, and uploads them with
[PEP 740](https://peps.python.org/pep-0740/) attestations through PyPI trusted
publishing. No API token exists anywhere.

## One-time setup

1. **Create the `pypi` environment** in the repository settings before any tag
   exists. Restrict deployments to tags matching `v*` and add a required
   reviewer. A workflow that references a missing environment creates it
   without protection rules.
2. **Configure the PyPI trusted publisher.** For the first upload, add a
   pending publisher on PyPI: project `whykit`, owner `CometWeb-io`,
   repository `whykit`, workflow `release.yml`, environment `pypi`. A pending
   publisher does not reserve the name. Follow the
   [PyPI guide](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).
3. **Protect version tags.** Add a tag ruleset for `v*` that limits creation to
   the maintainer and blocks deletion and updates, so a published version
   cannot be re-pointed.

## Cut a release

1. **Pick the version.** The data contract, the CLI and the lint rule codes are
   the public API. A renamed rule code or a format migration is a breaking
   change; see [`CHANGELOG.md`](../CHANGELOG.md).
2. **Open a release pull request** that changes only release bookkeeping:
   - set `__version__` in `src/whykit/__init__.py` to the final version, with
     no `.dev` suffix;
   - update the version and status line in [`README.md`](../README.md); keep
     the source-checkout install instructions until step 9 succeeds;
   - move the `[Unreleased]` entries into a dated section using the
     [changelog template](#changelog-section-template) below.
3. **Rehearse locally from a clean checkout** of the release branch:

   ```bash
   uv sync --locked
   uv run python -m unittest discover -s tests
   uv run whykit lint examples/northline --strict --today 2026-09-17
   export SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"
   uv build --out-dir dist
   uv build --out-dir dist-rebuild
   python3 scripts/check_dist.py dist --reproducible-against dist-rebuild
   uv run --locked --only-group dist twine check --strict dist/*
   ```

   `check_dist.py` prints one SHA-256 per archive. Keep them; step 7 compares
   against them. Delete `dist/` and `dist-rebuild/` afterwards, so they are not
   committed.
4. **Merge the pull request** once every required check is green. Then confirm
   that CI on the resulting `main` commit is green too; the release reruns the
   same gates and will fail if they do not pass there.
5. **Download the `distributions` artifact** from that `main` run and read the
   SBOM. It must list `whykit` at the release version and no other component.
6. **Tag the merged commit and push the tag.** This is the irreversible step:
   a version uploaded to PyPI can be yanked but never replaced.

   ```bash
   git switch main
   git pull --ff-only
   git tag -a vX.Y.Z -m "WhyKit X.Y.Z"
   git push origin vX.Y.Z
   ```

7. **Watch the release run.** The `build` job writes the archive digests to the
   run summary. They must match the digests from step 3: the build is
   reproducible, so a difference means the tagged tree is not the one you
   rehearsed. Stop and do not approve the deployment if they differ.
8. **Approve the `pypi` deployment** only after the `build` job is green and
   the digests match.
9. **Verify the published release:**
   - the PyPI project page renders the README, its links and the overview
     image, and shows the expected licence, classifiers and project URLs;
   - each file on PyPI shows a provenance attestation from
     `CometWeb-io/whykit`, workflow `release.yml`;
   - the SHA-256 digests on PyPI equal those from step 7;
   - a clean install works: `uv tool install whykit==X.Y.Z`, then
     `whykit --version`, `whykit init` into an empty directory and `whykit lint`
     there.
10. **Keep the SBOM.** Download the `release-sbom` artifact before it expires and
    attach it to the GitHub Release, if you create one.
11. **Open the next development cycle** in a follow-up pull request: bump
    `__version__` to the next `.dev0`, start an empty `[Unreleased]` section,
    and switch install instructions to the package index only after step 9
    passed.

## If something goes wrong

- **A gate fails before publishing.** Fix it on `main` through a pull request.
  Delete the unpublished tag only if no upload happened, then tag the fixed
  commit with the same version.
- **A broken version reached PyPI.** Yank it on PyPI with a reason, publish a
  fixed patch version, and record both in the changelog. Never reuse a
  version number.

## Keeping the build reproducible

- `hatchling` and `hatch-fancy-pypi-readme` are pinned exactly in
  `[build-system]`. Bump them deliberately in their own pull request; the CI
  rebuild check confirms the new backend is still deterministic.
- `SOURCE_DATE_EPOCH` must come from the commit (`git log -1 --format=%ct`),
  never from the clock.
- The README on PyPI links to `main` on GitHub. The link rewriting lives in
  `[tool.hatch.metadata.hooks.fancy-pypi-readme]` in `pyproject.toml`, and
  `check_dist.py` fails the build if a relative link survives it.

## Changelog section template

The integrator keeps `[Unreleased]` current during development. At release
time its entries move under a heading of this shape; drop subsections that are
empty.

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Breaking changes
- Renamed lint rule codes, format migrations, removed CLI options. Each entry
  says what an existing vault or pipeline has to change.

### Added
### Changed
### Deprecated
### Removed
### Fixed
### Security

[X.Y.Z]: https://github.com/CometWeb-io/whykit/compare/vPREVIOUS...vX.Y.Z
```

For the first package release there is no previous tag; link the tag itself
instead: `https://github.com/CometWeb-io/whykit/releases/tag/vX.Y.Z`.
