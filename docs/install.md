# Install

MoA-X runs inside **Claude Code** as a skill. You can also invoke the
orchestrator directly from a shell. Either way, you need four vendor
CLIs on your PATH, each authenticated. Any auth path the CLI itself
supports is fine. Subscription (OAuth / keychain) is what I run;
API-key auth works too.

## 1. Install the four CLIs

```bash
# OpenAI codex
npm i -g @openai/codex
codex login
# API-billed alternative:
# printenv OPENAI_API_KEY | codex login --with-api-key

# Anthropic Claude Code
# See https://docs.claude.com/en/docs/claude-code/quickstart
# API-billed alternative: export ANTHROPIC_API_KEY=...

# Google AGY / Antigravity
# Install or update Antigravity, then:
agy install
agy models

# OpenCode (drives Grok plus the Qwen/Kimi routes)
curl -fsSL https://opencode.ai/install | bash
# or: npm i -g opencode-ai
opencode auth login    # interactive login
# or export a provider key (no login needed):
#   export MOONSHOT_API_KEY=...    # Kimi
#   export QWEN_TOKEN_PLAN_API_KEY=sk-sp-...  # default Qwen refiner
```

The Balanced default roster uses Gemini 3.1 Pro, Grok 4.5, and GPT-5.6 Luna as
proposers; Qwen 3.8, Kimi K3, and Claude Opus 5 at `high` as refiners; and
GPT-5.6 Sol at `xhigh` as the aggregator. That keeps the default lane
lab-diverse across Google, xAI, OpenAI, Alibaba, Moonshot, and Anthropic.
Fable 5 1M at `xhigh` is an optional, quota-heavy Claude aggregator and
is never eligible as a proposer or refiner.

The built-in `qwen` refiner uses the Qwen Cloud Token Plan endpoint and
defaults to `qwen-token-plan/qwen3.8-max-preview`, with a 600-second cap.
Put the dedicated `sk-sp-...` credential in `.env` as
`QWEN_TOKEN_PLAN_API_KEY`. Do not combine a Token Plan key with the regular
DashScope pay-as-you-go endpoint; Qwen documents them as separate credential
and endpoint pairs. See [Qwen's OpenCode guide](https://docs.qwencloud.com/developer-guides/clients-and-developer-tools/opencode).

Subscription auth is the path I use and what the docs lead with. If
you'd rather bill through an API key, each vendor CLI already handles
that on its own; MoA-X defers to whatever auth state the CLI is in
when you invoke it. Authentication is already delegated to those CLIs; the
open gap is normalized usage/cost telemetry and safe pre-dispatch budget
controls across their different billing modes. See the contribution-priorities
section of the top-level README.

Cursor is not a supported MoA-X execution harness. Use the curated Grok route
through OpenCode and OpenAI routes through Codex.

### AGY model readiness

Google providers reuse existing local AGY authentication; MoA-X never prompts
for a key. Install AGY 1.1.5+, sign in interactively once, and verify
`agy models`. `agy-gemini-pro` is the only curated Gemini route and is part
of the default proposer roster. Live probes determine availability and fail
closed if the signed-in account does not expose it.

### Optional: Local Web UI and GitHub picker

```bash
# From a clean clone of this repository:
python3 -m venv .venv
.venv/bin/pip install -r requirements-web.txt
# Optional but recommended for image and scanned-PDF attachment OCR:
sudo apt install poppler-utils tesseract-ocr  # Ubuntu/Debian
# brew install poppler tesseract               # macOS
MOA_WEBUI_GITHUB_OWNER=your-github-user-or-org \
  .venv/bin/python -m harness.webui
```

The Web UI uses the same authenticated CLI state as the launching OS user. If
you want its repository picker, install and authenticate GitHub CLI:

```bash
gh auth status
```

`MOA_WEBUI_GITHUB_OWNER` is a single-user-or-organization allowlist, not a
cosmetic filter. It defaults to `drivelineresearch` for this upstream project;
forks and independent installs should set it explicitly. The server binds to
`127.0.0.1:7340` by default. See [`webui.md`](webui.md) for clean-clone
verification, XDG storage, uploads, profiles, bind controls, and the
trusted-network warning.

## 2. Verify

```bash
python3 harness/scripts/install_deps.py
```

The script checks each CLI's version and auth state. It does not
install anything or prompt for credentials. If something's missing,
it prints the exact `login` command to run yourself.

## 3. Install as a Claude Code skill (primary path)

The main way to run MoA-X is `/mixture-of-agents` inside Claude Code.
The release includes a ready-to-install archive whose top-level directory is
already named `mixture-of-agents/`:

```bash
MOA_X_VERSION=v0.4.1
curl -fsSL "https://github.com/drivelineresearch/moa-x/releases/download/${MOA_X_VERSION}/mixture-of-agents-${MOA_X_VERSION}.tar.gz" \
  | tar -xz -C ~/.claude/skills
```

Or clone the repository and copy `harness/` into your skills folder:

```bash
# From a clone of this repo:
cp -r harness ~/.claude/skills/mixture-of-agents
```

Restart Claude Code. `/mixture-of-agents` should now autocomplete.
See [`docs/usage.md`](usage.md) for what happens next.

## 4. Or run standalone (secondary)

The Python orchestrator works outside Claude Code too. You still need to
create the scout brief, but final aggregation can run as a recorded phase:

```bash
python3 harness/scripts/run_moa.py \
  --scout-brief path/to/your-scout-brief.json
```

After Layers 1 and 2 finish, either aggregate interactively or run only the
retained session's Codex-backed Layer 3:

```bash
python3 harness/scripts/run_moa.py \
  --scout-brief path/to/your-scout-brief.json \
  --phase layer3 \
  --aggregator-provider codex-sol \
  --aggregator-effort xhigh
```

See [`docs/usage.md`](usage.md#running-standalone) for the scout format,
phase behavior, and generated artifacts.

PRs that complete standalone Layer 0 from a raw spec or harden adapter
compatibility and recovery are welcome. See
[`CONTRIBUTING.md`](../CONTRIBUTING.md).

## Offline tests

```bash
python3 harness/scripts/test_offline.py
```

No network, no external CLIs. All tests should pass. CI runs the
same thing on every push.
