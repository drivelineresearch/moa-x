# GitHub release

Read this before packaging or publishing. Use the next semantic version based
on all changes since the previous tag; a docs cleanup can accompany accumulated
features. Publish from the exact merged commit after CI passes.

## Prepare and merge

1. Confirm a clean checkout and fetch origin. Preserve unrelated work separately.
2. Use a topic branch; never push directly to main.
3. Move Unreleased changelog entries into a dated version section and leave an
   empty Unreleased heading. Include the documentation changes.
4. Update the skill frontmatter version and the README/install release references.
5. Follow [verification](verification.md), inspect the diff, commit, push, and
   open a PR. Wait for CI and merge the verified head.
6. Fetch main and record the merged commit. Package from that commit, never a
   dirty working tree. Confirm the release tag is unused.

## Package

Produce these artifacts, replacing VERSION with the new tag:

| Artifact | Archive root and contents |
|---|---|
| moa-x-VERSION.tar.gz | moa-x-VERSION/ containing the tracked repository |
| mixture-of-agents-VERSION.tar.gz | mixture-of-agents/ containing the tracked repository plus a root skill manifest and compatibility links |
| SHA256SUMS | SHA-256 of both archives, using just their filenames |

The skill archive's root must work directly in the Claude Code skills directory.
Retain the harness/ package and repository docs so web/Slack imports and links
work after extraction. Copy the skill manifest to the archive root, adjusting
its parent-docs link to docs/. Add root links for scripts, prompts, report,
webui, slack, and config.example.yaml, each targeting its harness/ counterpart.
These internal links preserve existing skill commands without duplicating code.
The full archive retains the original repository layout. Neither archive may
include local configuration, credentials, session data, or caches.

Extract both into temporary directories. Verify safe archive paths, required
assets and schemas, version metadata, internal Markdown links, checksums, and
the offline tests from each extracted layout. Smoke-test web and Slack imports
using the optional dependencies. Inspect assets before uploading.

## Publish and verify

Create the GitHub release against the recorded merged commit with the three
artifacts attached. Describe changes since the previous release and include the
install command and checksum verification. Verify the tag resolves to the
merged commit, then download the published assets into a fresh directory and
verify their SHA-256 checksums against the local artifacts.

Publishing a GitHub release completes distribution. Deployments and service
restarts have their own operational procedure and require live runtime checks.
