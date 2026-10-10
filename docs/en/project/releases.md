[SmartGate](../../../README.md) / [Documentation](../README.md) / Releases

[English](releases.md) · [Русский](../../ru/project/releases.md)

# Release process

SmartGate's version is stored in `pyproject.toml`; user-facing changes belong in the root [CHANGELOG.md](../../../CHANGELOG.md). Tags use `vMAJOR.MINOR.PATCH`. A release is published only after its notes and artifacts have been reviewed.

## Prepare a draft

1. On a topic branch, update the package version and add one matching changelog entry. Record new behavior, upgrade steps and remaining limitations. Run the checks from the [development guide](../development/README.md).
2. Merge the reviewed change after successful CI. In **Actions → Prepare release → Run workflow**, select the default branch and enter the exact version without `v`, for example `0.1.0`.
3. The workflow rejects a malformed or mismatched version, missing/empty/duplicate changelog notes, failed tests or broken documentation links. It builds a wheel and source archive, creates an immutable-name tag at the workflow commit, and creates a **draft** release with both artifacts.
4. Review the draft's tag, commit, notes and artifacts, then publish it through GitHub's release page.

The workflow runs only manually on the default branch. It does not read the `smartgate` environment, use DNS credentials, change DNS records, or run state migrations. A tag or push does not automatically publish a release. Draft creation does create a tag, so the commit must be reviewed before running this command.

## Existing tags and interrupted preparation

Tag creation refuses an existing tag; the workflow never moves or overwrites it. If preparation stopped after creating a tag, inspect that tag's exact commit before continuing. Resume the draft manually from the verified existing tag and verified artifacts, or choose a new version for a new code change. Do not delete or retarget published tags to retry a failed run.

The first release describes the complete MVP. Later changelog entries describe changes since the last published version, including any required migrations. A draft is preparation, not evidence of a successfully published release.

[Upgrade a fork](../operations/upgrading.md) · [Roadmap](roadmap.md)
