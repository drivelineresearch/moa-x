"""Repository name resolution and deployment-owned GitHub access policy."""

import json
import os
import re
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from harness.webui import github
from harness.webui.launch import LaunchError


@dataclass(frozen=True)
class RepositoryPolicy:
    owner: str
    private_repositories: frozenset = frozenset()

    def __post_init__(self):
        github._validate_owner(self.owner)
        for pointer in self.private_repositories:
            if pointer == self.owner + "/*":
                continue
            github.parse_repo_pointer(pointer, self.owner)

    def permits_private(self, pointer):
        github.parse_repo_pointer(pointer, self.owner)
        approved = {entry.casefold() for entry in self.private_repositories}
        return pointer.casefold() in approved or (self.owner + "/*").casefold() in approved

    @classmethod
    def from_environment(cls, env=None):
        env = os.environ if env is None else env
        configured = env.get("MOA_SLACK_REPO_POLICY_FILE")
        path = Path(configured).expanduser() if configured else (
            Path(env.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "moa-x/repositories.json")
        if not path.exists() and not path.is_symlink() and not configured:
            return cls(env.get("MOA_WEBUI_GITHUB_OWNER") or github.DEFAULT_ALLOWED_OWNER,
                       frozenset(x.strip() for x in env.get("MOA_SLACK_PRIVATE_REPOS", "").split(",") if x.strip()))
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                raise ValueError("Policy must be a regular file smaller than 64 KiB.")
            if os.name == "posix" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise ValueError("Repository policy must belong to the service user and be mode 0600.")
            data = json.loads(path.read_text())
            if not isinstance(data, dict) or set(data) - {"owner", "private_repositories"}:
                raise ValueError("Unknown repository-policy keys.")
            owner = data.get("owner")
            private = data.get("private_repositories", [])
            if not isinstance(owner, str) or not isinstance(private, list) or not all(isinstance(x, str) for x in private):
                raise ValueError("Policy needs an owner and a list of private repository pointers.")
            if env.get("MOA_WEBUI_GITHUB_OWNER") and env["MOA_WEBUI_GITHUB_OWNER"] != owner:
                raise ValueError("Repository policy conflicts with the configured GitHub owner.")
            return cls(owner, frozenset(private))
        except (OSError, ValueError, github.GitHubWorkspaceError) as error:
            # Never print policy contents, private repository lists or file paths.
            raise ValueError("Invalid local Slack repository policy: " + type(error).__name__) from None


class RepositoryDirectory:
    def __init__(self, policy, *, listing=None, now=time.monotonic):
        self.policy = policy
        self.listing = listing or (lambda: github.list_repositories(policy.owner, limit=10000))
        self.now = now
        self.lock = threading.Lock()
        self.cache = {}
        self.expires = 0

    def catalog(self):
        with self.lock:
            if self.now() < self.expires:
                return self.cache
            try:
                rows = self.listing()
                found = {}
                for row in rows:
                    pointer = str(row.get("nameWithOwner") or "")
                    _, name = github.parse_repo_pointer(pointer, self.policy.owner)
                    found[name.lower()] = pointer
            except (ValueError, github.GitHubWorkspaceError, OSError):
                raise LaunchError("GitHub repository lookup is unavailable. Retry or provide a full GitHub repository URL.") from None
            self.cache, self.expires = found, self.now() + 300
            return self.cache

    def resolve(self, brief):
        owner = self.policy.owner
        # Context opt-outs are instructions, not names after a "repository"
        # marker. Ignore pointer text when detecting them so a repo literally
        # named task-only can still be addressed by its URL/owner pointer.
        context = re.sub(r"https?://\S+|\b" + re.escape(owner) + r"/[A-Za-z0-9_.-]+", "", brief, flags=re.I)
        no_repo = re.search(r"\b(?:no|without)\s+(?:(?:a|an|any|the)\s+)?repo(?:sitory)?\b", context, re.I)
        task_only = re.search(r"\btask[- ]only\b", context, re.I)
        named_task_only = re.search(r"\brepo(?:sitory)?\s+[`\"']?task[- ]only\b", context, re.I)
        if no_repo or (task_only and not named_task_only):
            return None
        pointers = set()
        # Slack link labels and /tree/... suffixes do not change the repo owner/name.
        for actual_owner, name in re.findall(r"https?://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+)", brief, re.I):
            if actual_owner.lower() != owner.lower():
                raise LaunchError("Repository context must belong to the configured GitHub organization.")
            pointers.add(owner + "/" + name.removesuffix(".git").rstrip("."))
        pointers.update(owner + "/" + name.removesuffix(".git").rstrip(".") for name in
                        re.findall(r"\b" + re.escape(owner) + r"/([A-Za-z0-9_.-]+)", brief, re.I))
        # Explicit pointers take precedence over incidental bare repo names.
        if pointers:
            for pointer in pointers:
                github.parse_repo_pointer(pointer, owner)
            return self._one(pointers)

        marked = set(re.findall(r"\brepo(?:sitory)?\s+[`\"']?([A-Za-z0-9_.-]+)", brief, re.I))
        candidates = set(marked)
        for match in re.finditer(r"(?<![\w/.-])([._-]*[A-Za-z0-9][A-Za-z0-9_.-]*)(?![\w/-])", brief):
            name = match.group(1).rstrip(".")
            prefix = brief[max(0, match.start() - 60):match.start()]
            distinctive = "-" in name or "_" in name or "." in name
            contextual = bool(re.search(r"(?:review|audit|inspect|analy[sz]e|look (?:at|into)|check)\s+(?:the\s+)?[`\"']?$", prefix, re.I))
            quoted = match.start() > 0 and brief[match.start() - 1] in "`\"'"
            # Do not turn everyday task vocabulary into implicit repository access.
            if name.lower() in {"api", "data", "docs", "tests", "test", "config", "examples", "research"} and name not in marked:
                continue
            if name.lower() == "moa-x" and re.search(r"(?:use|using|with|through|via|run)\s+$", prefix, re.I):
                continue
            if distinctive or contextual or quoted:
                candidates.add(name)
        if not candidates:
            return None
        catalog = self.catalog()
        for name in candidates:
            pointer = catalog.get(name.rstrip(".").lower())
            if pointer:
                pointers.add(pointer)
            elif name in marked:
                raise LaunchError("No accessible repository matches that name in the configured GitHub organization. Check the name or provide its full URL.")
        return self._one(pointers)

    @staticmethod
    def _one(pointers):
        if len(pointers) > 1:
            raise LaunchError("Choose one repository per run; the brief names several repositories.")
        return next(iter(pointers), None)
