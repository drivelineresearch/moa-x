# Scout brief prompt — Mixture of Agents Layer 0 (parent Claude, in-place)

This prompt is for the parent Claude session that runs `/mixture-of-agents`.
It runs BEFORE any external CLI is invoked. The goal of Layer 0 is to bound
the problem so the expensive external runs are not wandering randomly through
the repo.

You produce a single artifact: `.moa/<session>/scout-brief.json`, a
small JSON document that codex and gemini both consume verbatim.

## Steps

### 1. Read the spec
The user invoked `/mixture-of-agents` either with `--spec FILE` or with the
spec text inline. Read it carefully. Understand what they actually want.

### 2. Ask 1-3 clarifying questions if needed
Use the `AskUserQuestion` tool. The bar for asking: would the answer
materially change what a frontier model produces in its plan? If yes, ask.
If no, do not waste a turn.

Examples of questions worth asking:
- "Should this be a new file or an extension of an existing module?"
- "Is this for production deployment or local prototyping?"
- "Do you want backward-compatibility with X, or is breaking change OK?"

Examples NOT worth asking:
- "Should the code be readable?" (yes, always)
- "What language?" (look at the repo)
- "Do you want tests?" (look at the repo's testing convention)

Cap clarifying questions at 3. If the spec is so ambiguous you need
more, the spec itself is broken. Note that in the brief and proceed
with your best interpretation.

### 3. Identify the focus set
Read the repo. Identify 5-15 files (or directories) that the proposers
should focus on. These are:

- Files the spec explicitly mentions
- Files that define interfaces the new code will touch
- Files that contain similar prior implementations (proposers should reuse
  rather than reinvent)
- Files that contain conventions or invariants the new code must respect
- Tests for the area being changed

Use Glob, Grep, and Read freely. This step is on the parent session's dime,
not the external CLIs', so dig as much as is useful.

### 4. Identify out-of-scope items
What should the proposers explicitly NOT touch? Examples:
- Other modules of the codebase that are not relevant
- Database migration code if the change is non-database
- Platform-specific files if the change is platform-agnostic
- Test infrastructure if you are only adding tests

Out-of-scope items prevent expensive wandering.

### 5. Identify focus topics
3-5 areas of concern that the proposers should think about. Examples:
- "Concurrency safety with the existing job queue"
- "Compatibility with Python 3.10+"
- "Following the existing request-handler pattern"
- "Cost minimization for LLM calls"

### 6. Write the scout brief
Save to `.moa/<session>/scout-brief.json`:

```json
{
  "session_id": "YYYYMMDD-HHMMSS-<slug>",
  "frozen_spec": "<the user's request, verbatim or lightly cleaned>",
  "clarifications_resolved": [
    {"question": "...", "answer": "..."}
  ],
  "focus_files": ["path/to/file.py", "path/to/dir/", ...],
  "focus_topics": ["topic 1", "topic 2", ...],
  "in_scope": ["thing to do 1", "thing to do 2", ...],
  "out_of_scope": ["do not touch X", "do not modify Y", ...],
  "repo_path": "/absolute/path/to/repo",
  "exploration_budget": {
    "max_file_reads": 12,
    "max_grep_calls": 6,
    "max_minutes": 4
  }
}
```

### 7. Show the brief to the user
Render the brief in markdown for human review. Then ask via `AskUserQuestion`:
"Scout brief looks like this. Run codex + gemini + sonnet proposers (3
parallel) + codex + gemini broadcast refiners (2 parallel, each sees all 3
proposals) now? Estimated 6-12 minutes wall-clock."

A dollar cost estimate is optional. If the user is on subscription
plans (the common case), there's nothing to estimate. If they're
running any of the CLIs on API-billed auth, surface that you can't
predict exact spend without token accounting, which the orchestrator
doesn't meter yet.

### 8. On approval, hand off to the orchestrator
Invoke the Python orchestrator via the Bash tool:
```bash
python ~/.claude/skills/mixture-of-agents/scripts/run_moa.py \
  --scout-brief .moa/<session>/scout-brief.json
```

The orchestrator runs Layers 1 and 2 (proposers + refiners) and writes
`.moa/<session>/synthesis-input.md`. When it returns, proceed to Layer 3
(in-place aggregation in this same session).
