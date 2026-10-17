# Native Muse Code and DeepSeek Harness

The optional `muse-native` and `deepseek-native` routes run the vendors' own
harnesses. They supplement the OpenCode routes and do not change the default
roster, existing profiles, frozen jobs, or archived provenance. Both are
available as proposers and broadcast refiners; the existing aggregator choices
remain unchanged.

## Install and authorize

Muse Code (verified with 1.4.10):

```bash
curl -fsSL https://dev.meta.ai/install.sh -o /tmp/muse-install.sh
bash /tmp/muse-install.sh
muse --version
muse login
```

`muse login` prints a browser authorization flow. An API key is also supported:
run `muse auth set --api-key-stdin` and supply the key through stdin, or set
`META_API_KEY` in the invoking process. Muse's stored `auth.json` is copied
privately into each isolated invocation; personal settings, plugins and hooks
are not copied. Environment API keys take priority over stored login state.
MoA-X serializes its native Muse credential copies and saves refreshed browser
credentials atomically, preserving any concurrent external login change.
Keep keys out of prompts, repository configuration and command arguments.

DeepSeek Harness (the current 1017-compatible release candidate):

```bash
npm install -g @deepseek-ai/dsh@0.2.1-rc.2
dsh --version
```

Use a **DeepSeek API key** from <https://platform.deepseek.com/api_keys>.
An OpenCode Go key cannot authorize native DeepSeek calls. For this machine's
Web UI, save the native key locally without putting it in shell history:

```bash
mkdir -p ~/.dsh
chmod 700 ~/.dsh
read -rsp 'DeepSeek API key: ' moax_deepseek_key; printf '\n'
(umask 077; printf 'DEEPSEEK_API_KEY=%s\n' "$moax_deepseek_key" > ~/.dsh/.env)
unset moax_deepseek_key
```

This replaces `~/.dsh/.env`; if it already holds other settings, edit that file
locally and add the one variable instead. Alternatively, export
`DEEPSEEK_API_KEY` in the process launching MoA-X. An explicitly empty
environment value disables the file fallback, matching native headless rules.
`DSH_HOME` changes the native credential directory. The adapter reads only
`DEEPSEEK_API_KEY` from that file, without evaluating shell substitutions.

The native headless profile intentionally ignores credentials saved by the
DeepSeek GUI. The adapter uses launch credentials and a disposable `DSH_HOME`,
so GUI login alone does not authorize MoA-X. The `deepseek-flash` native model
name is DeepSeek's documented rolling route to V4.1 Flash-1017; a future
upstream alias change can update the served checkpoint again.

## Select the native routes

After the prepared release is deployed, the Web UI lists **Muse Spark 1.3 · Muse
Code** and **DeepSeek Flash · Native Harness** in their Meta and DeepSeek lab
groups, with authorization guidance until credentials are present. Each route
keeps its lab's avatar and full-body illustration regardless of execution
harness. Native efforts are Muse's eight CLI tiers and DeepSeek's `low`, `high`,
and `max`. Custom provider definitions can also target `harness: muse` or
`harness: deepseek`.

```bash
python3 harness/scripts/run_moa.py --help
# Add muse-native/deepseek-native to --proposers or --refiners for a new run.
python3 harness/scripts/install_deps.py
python3 harness/scripts/test_native.py
```

`MOA_MUSE_BIN` / `MOA_DEEPSEEK_BIN` select executables;
`MOA_MUSE_TIMEOUT` / `MOA_DEEPSEEK_TIMEOUT` select default invocation budgets.
Native subscriptions, API access and billing are separate from OpenCode Go.
Credential presence is a local readiness hint; verify a live structured call
after authorization before relying on a route.

## Execution contract

Every call gets an independent repository snapshot and temporary native
configuration. Muse uses its JSONL terminal event, output schema, disabled shell
and writes, no foreign personal context, no reminder agents, and an agent
capacity of one. Workflows and retained sessions are disabled, and configuration,
data and state live in the disposable directory. Repair selects the empty tool
preset and disables web tools.
DeepSeek uses `headless --json` and stdin, an isolated home and explicit native
model/effort selection. Every shipped tool plugin except read/search/web is
disabled; filesystem write/edit and shell/subagent/plugin/workflow tools are
disabled. Repair disables all tool plugins. Native provider retries are
disabled so the shared MoA-X recovery deadline remains authoritative.

Only completed terminal answers may become structured results. Tool text,
reasoning and partial output never become a successful plan; nonzero exits,
terminal failures and timeouts fail closed. The usual schema, evidence,
workspace-mutation and bounded recovery checks still apply.

## Sources

- [Muse Code](https://dev.meta.ai/docs/muse-code)
- [Muse authentication](https://dev.meta.ai/docs/muse-code/auth)
- [DeepSeek CLI](https://github.com/deepseek-ai/deepseek-harness/tree/master/apps/cli)
- [DeepSeek headless authentication and events](https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/bundle/headless)
- [DeepSeek 1017 update](https://api-docs.deepseek.com/updates/)
