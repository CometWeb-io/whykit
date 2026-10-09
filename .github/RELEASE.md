# Publishing WhyKit

This checklist covers two separate events: making the GitHub source repository
public and, later, publishing the first package to PyPI. Verify the exact commit
before changing repository visibility; a tag is needed only for the package
release. A local green test run does not substitute for successful GitHub checks
on the revision being published.

## Before making the repository public

- Review every branch and tag that will become visible, including pull-request
  refs where available. Check commit author and committer identities, attribution
  trailers, deleted or renamed files in commit history, and source content. A
  file removed from the current tree remains available in older commits. Decide
  whether author and committer addresses are intended to become public. CI
  rejects AI-tool identities and attribution trailers in commit metadata, but
  that check cannot remove old Actions runs.
- Review issues, pull requests, comments, reviews, Actions runs, logs and
  artifacts. A private-to-public change makes repository activity and Actions
  history visible. Gitleaks checks for credential patterns; it does not identify
  personal data, validate example claims or inspect every hosted record.
- Keep sample organizations, people, evidence, metrics and decisions clearly
  fictional. Use reserved domains such as `example.com`, `.example`, `.invalid`
  or `.test`; do not attach invented claims to real organizations.
- Confirm the contact channels in `SECURITY.md` and `CODE_OF_CONDUCT.md` are
  monitored. The email fallback must work before changing visibility. GitHub
  private vulnerability reporting can be enabled only after the repository is
  public; enable it immediately after the visibility change and verify that
  the reporting form is available.
- Require successful CI for the exact `main` commit that will become public.
  Resolve failures and review the final Actions logs before changing visibility.
- Decide how to handle any sensitive history or hosted records with the
  maintainer. Do not rewrite shared history or delete run records without an
  explicit decision and coordination plan.
- If history is rewritten while the repository is still private, coordinate
  affected clones and refs, then re-check pull requests and Actions records that
  still point to the old commits before changing visibility.

Changing repository visibility is an external, hard-to-reverse action. Review
GitHub's [visibility-change consequences](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility)
and obtain maintainer approval before proceeding.

After the repository is public, enable private vulnerability reporting and
configure a `main` ruleset: require pull requests, block force-pushes and branch
deletion, and require the checks from a successful run of the release
candidate's CI workflow. Require the Explorer and Explorer end-to-end checks.
Verify the saved ruleset in GitHub; do not infer
that protection is active from this checklist.

Before changing visibility, confirm that the README and usage guide contain no
private-preview or access-required notice. Until the first PyPI release, keep
the source-checkout install path and do not recommend a package-index install.
Do not claim real-vault use before that trial has happened. Keep a local
[source-bound dogfood record](../docs/dogfood.md) without copying operating
notes into this repository.

## Verify a release candidate

The step-by-step package release, including reproducible builds, the SBOM and
attestation checks, is the numbered checklist in
[docs/releasing.md](../docs/releasing.md). The notes below summarize the gates.

Run the checks in `CONTRIBUTING.md` from a clean checkout. The CI workflow also
tests Python 3.11–3.14, Windows portability, the optional MCP client, wheel and
source-distribution installs, example-vault policies, and secret scanning. The
release workflow reuses those gates against the exact tag and publishes only if
every required gate passes. Explorer build, renderer, end-to-end and
accessibility checks also block the product release.

Before tagging, confirm all of the following:

- The package version in `src/whykit/__init__.py` matches the tag (`vX.Y.Z`).
- The tagged commit is reachable from `main` and its working tree passes the
  release profile.
- GitHub reports successful required checks for that exact tag.
- The GitHub `pypi` environment is restricted to release tags and has an
  independent reviewer configured. Do not push a release tag while that
  environment is absent: a workflow referencing a missing environment can
  create it without protection rules. See GitHub's
  [environment guidance](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).

## Configure PyPI publishing

For the first upload, configure a pending Trusted Publisher with project
`whykit`, GitHub owner `CometWeb-io`, repository `whykit`, workflow
`release.yml`, and environment `pypi`. A pending publisher does not reserve the
project name; it is claimed only after a successful first upload. Follow the
[PyPI setup guide](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
and verify the publisher settings before releasing.

The release workflow checks that the tag matches the package version. After
the candidate is approved and all gates are green, publish the matching tag:

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

Do not recommend `uv tool install whykit` or `pipx install whykit` until the
first upload succeeds and the package page is confirmed to belong to this
project.

## Establish the product claim

Use WhyKit on a real, private vault before saying the project is used for
CometWeb decisions. Keep the vault private unless every included source is
intentionally public. WhyKit checks structure and traceability; it does not
verify that a source or conclusion is true.
