#!/usr/bin/env python3
"""Check GitHub for a newer version and update in place (stdlib only).

Used by the page's "Check for updates" / "Update" buttons (``usage_history.py serve``):

* ``check(repo)`` runs ``git fetch --quiet origin main`` (with a timeout) and compares HEAD with
  ``origin/main``. It never changes the working tree.
* ``spawn(...)`` starts a detached updater (``self_update.py run``) and returns at once. The updater
  stops the dashboard, waits for the old server to exit, runs ``git pull --ff-only origin main``,
  reinstalls the requirements and starts the dashboard again. Every step goes to
  ``<data_dir>/update.log``. If a step fails it still starts the dashboard (old or new code), so the
  page is not left dead.

Only fast-forwards: no force, no stash. ``usage-data/`` is gitignored and never touched.

Before an update the current commit and version are saved to ``<data_dir>/update_prev.json`` (local, not in
git). ``rollback`` (the page's "Roll back to previous version") moves the ``main`` branch back to that commit
with ``git reset --keep`` -- still on ``main``, no detached HEAD, so the next update is a normal fast-forward
again. It refuses when program files have local edits (they are never discarded), when the saved commit is not
an ancestor of HEAD, or when it is not on ``main``. Untracked / ignored files (usage-data/, usage_config.json)
are never touched.

``changelog_since(repo)`` reads the English CHANGELOG of the version that the last check fetched
(``git show origin/main:CHANGELOG.md``; the notes are English in every page language): no extra network request, only what ``git fetch`` already brought.
The *check* runs by itself about once a day unless ``"update_check": "off"`` (or the page's switch is off);
an *update* only happens when someone presses the button.

    python self_update.py check  [--no-fetch]
    python self_update.py run    --data-dir DIR [--config FILE] [--wait-pid PID] [--rollback]   # what the buttons start
    python self_update.py previous --data-dir DIR   # the commit "Roll back" would return to
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import proc_util  # noqa: E402

REMOTE, BRANCH = "origin", "main"
FETCH_TIMEOUT = 20
STEP_TIMEOUT = {"stop": 120, "pull": 120, "install": 900, "start": 180}
LOCK_NAME, LOG_NAME = "update.lock", "update.log"
LOG_MAX_BYTES = 500_000

# check() states; only "behind" can be applied
UP_TO_DATE, BEHIND, NOT_GIT, NO_GIT, NO_REMOTE, FETCH_FAILED = "up_to_date", "behind", "not_git", "no_git", "no_remote", "fetch_failed"
LOCAL_CHANGES, DIVERGED, WRONG_BRANCH, GIT_ERROR = "local_changes", "diverged", "wrong_branch", "git_error"


class GitError(Exception):
    pass


def _no_window():
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)} if os.name == "nt" else {}


def _git_env():
    # never wait for a password prompt or a credential window: fail instead
    return dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GIT_ASKPASS="", SSH_ASKPASS="",
                LC_ALL="C", LANG="C")


def git(repo, *args, timeout=30, git_bin="git"):
    """stdout of ``git -C repo args`` (stripped). FileNotFoundError when git is missing, GitError on failure."""
    try:
        r = subprocess.run([git_bin, "-C", str(repo)] + list(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout, env=_git_env(), **_no_window())
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} timed out after {timeout}s")
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitError((err[-1] if err else f"git {args[0]} exited with {r.returncode}")[:300])
    return r.stdout.decode("utf-8", "replace").strip()


def git_bytes(repo, *args, timeout=30, git_bin="git"):
    """Raw stdout bytes of ``git -C repo args`` (no decoding, no stripping); GitError on failure."""
    try:
        r = subprocess.run([git_bin, "-C", str(repo)] + list(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout, env=_git_env(), **_no_window())
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} timed out after {timeout}s")
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitError((err[-1] if err else f"git {args[0]} exited with {r.returncode}")[:300])
    return r.stdout


def _lf(b):
    return b.replace(b"\r\n", b"\n")


def changed_files(repo, git_bin="git"):
    """(real, eol_only): tracked files that differ from HEAD (staged or not). ``eol_only`` are files whose only
    difference is CRLF vs LF, e.g. a blob committed with CRLF while .gitattributes says ``eol=crlf`` (git then
    reports the file as modified right after checkout). Compared here byte by byte, CRLF -> LF on both sides."""
    raw = git_bytes(repo, "diff", "--name-only", "-z", "HEAD", git_bin=git_bin)
    real, eol = [], []
    for name in [n.decode("utf-8", "surrogateescape") for n in raw.split(b"\0") if n]:
        p = Path(repo) / name
        same = False
        if p.is_file() and not p.is_symlink() and not any(c.isspace() for c in name):
            try:
                same = _lf(p.read_bytes()) == _lf(git_bytes(repo, "show", f"HEAD:{name}", git_bin=git_bin))
            except (OSError, GitError):
                same = False
        (eol if same else real).append(name)
    return real, eol


def _same_path(a, b):
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except OSError:
        return False


def check(repo=HERE, fetch=True, git_bin="git", remote=REMOTE, branch=BRANCH, fetch_timeout=FETCH_TIMEOUT):
    """{"ok", "state", "can_update", "behind", "ahead", "head", "latest": {"hash", "subject"}, "detail"}.

    ``ok`` is False only when the check itself could not run (not a git checkout, no git, offline...)."""
    repo = Path(repo)
    out = {"ok": False, "state": GIT_ERROR, "can_update": False, "behind": 0, "ahead": 0, "head": None,
           "latest": None, "detail": ""}
    if not (repo / ".git").exists():  # ZIP download (or a copy without .git)
        return dict(out, state=NOT_GIT)
    try:
        top = git(repo, "rev-parse", "--show-toplevel", git_bin=git_bin)
    except FileNotFoundError:
        return dict(out, state=NO_GIT)
    except GitError as exc:
        return dict(out, state=NOT_GIT, detail=str(exc))
    if not _same_path(top, repo):  # the folder sits inside some other repository
        return dict(out, state=NOT_GIT, detail="the folder is inside another git repository")
    try:
        out["head"] = git(repo, "rev-parse", "--short", "HEAD", git_bin=git_bin)
        try:
            git(repo, "remote", "get-url", remote, git_bin=git_bin)
        except GitError:
            return dict(out, state=NO_REMOTE, detail=f"no '{remote}' remote")
        if fetch:
            try:
                git(repo, "fetch", "--quiet", remote, branch, timeout=fetch_timeout, git_bin=git_bin)
            except GitError as exc:
                return dict(out, state=FETCH_FAILED, detail=str(exc))
        ref = f"refs/remotes/{remote}/{branch}"
        try:
            git(repo, "rev-parse", "--verify", "--quiet", ref, git_bin=git_bin)
        except GitError:
            return dict(out, state=FETCH_FAILED, detail=f"{remote}/{branch} not fetched yet")
        counts = git(repo, "rev-list", "--left-right", "--count", f"HEAD...{ref}", git_bin=git_bin).split()
        ahead, behind = int(counts[0]), int(counts[1])
        h, _, subject = git(repo, "log", "-1", "--format=%h%x00%s", ref, git_bin=git_bin).partition("\0")
        out.update(ok=True, ahead=ahead, behind=behind, latest={"hash": h, "subject": subject})
        if behind == 0:
            return dict(out, state=UP_TO_DATE)
        cur = git(repo, "rev-parse", "--abbrev-ref", "HEAD", git_bin=git_bin)
        if cur != branch:
            return dict(out, state=WRONG_BRANCH, detail=f"current branch is {cur}")
        if ahead:
            return dict(out, state=DIVERGED, detail=f"{ahead} local commit(s) not on {remote}/{branch}")
        files, eol_only = changed_files(repo, git_bin=git_bin)  # staged + unstaged, tracked files
        if files:
            return dict(out, state=LOCAL_CHANGES, detail="changed: " + ", ".join(files[:5]) + (" …" if len(files) > 5 else ""))
        if eol_only:  # line endings only (not a real edit): the updater clears these before pulling
            out["eol_only"] = eol_only
        return dict(out, state=BEHIND, can_update=True)
    except FileNotFoundError:
        return dict(out, state=NO_GIT)
    except (GitError, ValueError, IndexError) as exc:
        return dict(out, ok=False, state=GIT_ERROR, detail=str(exc))


# ------------------------------------------------------------------ automatic check (on by default)
AUTO_EVERY = 24 * 3600    # at most one automatic "git fetch" a day (manual checks count too)
SETTING_KEY = "update_check"  # usage_config.json: "auto" (default) or "off"
SETTING_FILE = "update_check.json"  # used instead when the dashboard runs without a config file
LAST_FILE = "update_last.json"  # last check result + time, so a restart does not fetch again


def _write_json(p, obj, indent=1):
    p = Path(p)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")
    os.replace(tmp, p)


def _read_json(p):
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def auto_enabled(cfg, data_dir):
    """True unless the config (or, without a config file, <data_dir>/update_check.json) says "off"."""
    v = (cfg or {}).get(SETTING_KEY)
    if v is None:
        v = (_read_json(Path(data_dir) / SETTING_FILE) or {}).get(SETTING_KEY)
    if isinstance(v, bool):
        return v
    return str(v if v is not None else "auto").strip().lower() not in ("off", "false", "no", "0", "manual")


def set_auto(cfg_path, data_dir, on):
    """Save the page's on/off switch: into usage_config.json when there is one (other keys kept as they are),
    else into <data_dir>/update_check.json. Returns the file written."""
    value = "auto" if on else "off"
    if cfg_path and Path(cfg_path).exists():
        cfg = _read_json(cfg_path)
        if cfg is None:
            raise ValueError(f"cannot read {Path(cfg_path).name}")
        cfg[SETTING_KEY] = value
        _write_json(cfg_path, cfg, indent=2)
        return Path(cfg_path)
    p = Path(data_dir) / SETTING_FILE
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    _write_json(p, {SETTING_KEY: value})
    return p


def load_last(data_dir):
    """(checked_at epoch seconds, result or None) of the last check, kept across restarts."""
    d = _read_json(Path(data_dir) / LAST_FILE) or {}
    try:
        return float(d.get("checked_at") or 0), d.get("result")
    except (TypeError, ValueError):
        return 0.0, None


def save_last(data_dir, result, when=None):
    try:
        _write_json(Path(data_dir) / LAST_FILE, {"checked_at": time.time() if when is None else when, "result": result})
    except OSError:
        pass


def auto_due(last_checked, now=None):
    return (time.time() if now is None else now) - (last_checked or 0) >= AUTO_EVERY


# ------------------------------------------------------------------ version and changelog
VERSION_FILE = "VERSION"
CHANGELOG = "CHANGELOG.md"  # update notes are English only (CHANGELOG.zh-TW.md is no longer maintained)
NOTES_MAX_CHARS, NOTES_MAX_ENTRIES = 3000, 6
_HEAD_RE = re.compile(r"^## \[v?(\d+(?:\.\d+){0,3})\][^\n]*$", re.M)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")


def parse_version(v):
    """"1.2.3" / "v1.2" -> (1, 2, 3) / (1, 2, 0); None when it is not a version."""
    m = re.fullmatch(r"\s*v?(\d+(?:\.\d+){0,3})\s*", str(v or ""))
    if not m:
        return None
    t = tuple(int(x) for x in m.group(1).split("."))
    return t + (0,) * (3 - len(t)) if len(t) < 3 else t


def read_version(repo=HERE, rev=None, git_bin="git"):
    """Version string from the VERSION file of the working tree, or of commit ``rev``; None if missing."""
    try:
        text = git(repo, "show", f"{rev}:{VERSION_FILE}", git_bin=git_bin) if rev else \
            (Path(repo) / VERSION_FILE).read_text(encoding="utf-8")
    except (OSError, GitError):
        return None
    text = text.strip().splitlines()[0].strip() if text.strip() else ""
    return text if parse_version(text) else None


def parse_changelog(text):
    """[(version tuple, entry text)] for every ``## [x.y.z] - date`` heading, in file order (newest first)."""
    text = str(text or "").replace("\r\n", "\n")
    heads = list(_HEAD_RE.finditer(text))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        nxt = re.search(r"^(?:## |\[[^\]]+\]:\s)", text[m.end():end], re.M)  # stop at link references
        body = text[m.start():m.end() + nxt.start() if nxt else end]
        out.append((parse_version(m.group(1)), body.strip()))
    return out


def _plain_inline(t):
    """Markdown inline syntax -> plain words: [text](url) -> text, `code` -> code, **b** / *i* / __b__ -> b."""
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", t)
    t = t.replace("`", "")
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)
    return re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", t)


def to_plain(entry, cjk=False):
    """One CHANGELOG entry as clean plain text: "## [1.1.0] - 2026-10-25" -> "v1.1.0 (2026-10-25)"
    (CJK: "v1.1.0（2026-10-25）"), "### Added" -> "Added:" ("新增："), "- item" -> "・item" with wrapped
    continuation lines joined; backticks, emphasis and link syntax removed."""
    out = []
    for raw in entry.splitlines():
        line = raw.rstrip()
        m = re.match(r"^##\s+\[v?([^\]]+)\]\s*(?:[-–—]\s*)?(.*)$", line)
        if m:
            date = _plain_inline(m.group(2)).strip()
            out.append(f"v{m.group(1)}" + ((f"（{date}）" if cjk else f" ({date})") if date else ""))
            continue
        m = re.match(r"^#{1,6}\s+(.*?)\s*#*$", line)
        if m:
            h = _plain_inline(m.group(1)).strip().rstrip(":：")
            if out and out[-1] != "":
                out.append("")
            out.append(h + ("：" if cjk else ":"))
            continue
        m = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if m:
            out.append("・" + _plain_inline(m.group(1)).strip())
            continue
        if line.strip() and raw[:1].isspace() and out and out[-1].startswith("・"):  # wrapped bullet text
            out[-1] += ("" if cjk else " ") + _plain_inline(line.strip())
            continue
        out.append(_plain_inline(line.strip()))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return text


_UNRELEASED_RE = re.compile(r"##\s*\[[^\]]*\]\s*[-–—]\s*unreleased\b", re.I)


def notes_between(text, current, cap=NOTES_MAX_CHARS, max_entries=NOTES_MAX_ENTRIES, cjk=False):
    """Plain text (see ``to_plain``) of the entries newer than ``current`` (only the newest one when ``current``
    is unknown), control / bidi characters removed, at most ``cap`` characters."""
    cur = parse_version(current)
    entries = [e for v, e in parse_changelog(text) if (cur is None or v > cur) and not _UNRELEASED_RE.match(e)]  # "## [1.11.0] - Unreleased" is not news yet
    entries = entries[:1] if cur is None else entries[:max_entries]
    out = "\n\n".join(to_plain(_CTRL_RE.sub("", e), cjk) for e in entries)
    return out if len(out) <= cap else out[:cap - 1].rstrip() + "…"


def changelog_since(repo=HERE, lang="en", ref=f"{REMOTE}/{BRANCH}", current=None, git_bin="git"):
    """What changed between this version and ``ref`` (normally origin/main as fetched by the last check), from
    the English CHANGELOG.md whatever the page language (``lang`` is accepted and ignored; only the panel heading
    around the notes is translated). "" when there is nothing to show. Reads git objects only (``git show``): no network."""
    current = current if current is not None else read_version(repo)
    try:
        text = git(repo, "show", f"{ref}:{CHANGELOG}", git_bin=git_bin)
    except (OSError, GitError):
        return ""
    return notes_between(text, current)


# ------------------------------------------------------------------ previous version (rollback)
PREV_FILE = "update_prev.json"
NO_PREVIOUS, ALREADY, NOT_ANCESTOR, MISSING = "no_previous", "already_there", "not_ancestor", "missing_commit"


def record_previous(repo, data_dir, git_bin="git"):
    """Save the current commit, branch and version to <data_dir>/update_prev.json (called right before an update)."""
    rec = {"commit": git(repo, "rev-parse", "HEAD", git_bin=git_bin),
           "branch": git(repo, "rev-parse", "--abbrev-ref", "HEAD", git_bin=git_bin),
           "version": read_version(repo), "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    _write_json(Path(data_dir) / PREV_FILE, rec)
    return rec


def load_previous(data_dir):
    d = _read_json(Path(data_dir) / PREV_FILE) or {}
    c = str(d.get("commit") or "")
    return d if re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", c) else None


def rollback_check(repo, data_dir, git_bin="git"):
    """{"can_rollback", "state", "commit", "short", "version", "eol_only", "detail"}; local git only."""
    repo = Path(repo)
    prev = load_previous(data_dir)
    out = {"can_rollback": False, "state": NO_PREVIOUS, "commit": None, "short": None, "version": None, "detail": ""}
    if not prev:
        return out
    out.update(commit=prev["commit"], short=prev["commit"][:7], version=prev.get("version"))
    if not (repo / ".git").exists():
        return dict(out, state=NOT_GIT)
    try:
        if git(repo, "rev-parse", "HEAD", git_bin=git_bin) == prev["commit"]:
            return dict(out, state=ALREADY)
        cur = git(repo, "rev-parse", "--abbrev-ref", "HEAD", git_bin=git_bin)
        if cur != BRANCH or prev.get("branch") not in (None, BRANCH):
            return dict(out, state=WRONG_BRANCH, detail=f"current branch is {cur}")
        try:
            git(repo, "cat-file", "-e", prev["commit"] + "^{commit}", git_bin=git_bin)
        except GitError:
            return dict(out, state=MISSING)
        try:  # only ever go back along the history the update moved forward on
            git(repo, "merge-base", "--is-ancestor", prev["commit"], "HEAD", git_bin=git_bin)
        except GitError:
            return dict(out, state=NOT_ANCESTOR)
        files, eol_only = changed_files(repo, git_bin=git_bin)
        if files:
            return dict(out, state=LOCAL_CHANGES, detail="changed: " + ", ".join(files[:5]) + (" …" if len(files) > 5 else ""))
        return dict(out, state="ok", can_rollback=True, eol_only=eol_only)
    except FileNotFoundError:
        return dict(out, state=NO_GIT)
    except GitError as exc:
        return dict(out, state=GIT_ERROR, detail=str(exc))


# ------------------------------------------------------------------ updater
def updater_busy(data_dir):
    """Pid of a running updater (from <data_dir>/update.lock), else None."""
    try:
        pid = int((Path(data_dir) / LOCK_NAME).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if proc_util.pid_alive(pid) and pid != os.getpid() else None


def use_windows_scripts(repo):
    """Windows installs made with install.ps1 (.venv) update with the same .ps1 scripts the user runs."""
    repo = Path(repo)
    return os.name == "nt" and (repo / ".venv" / "Scripts" / "python.exe").exists() and (repo / "install.ps1").exists()


def plan(repo, data_dir, config=None, with_sampler=True, windows=None, python=None, requirements_changed=True):
    """[(name, argv or None)] for stop / pull / install / start. ``install`` is None when there is nothing to do."""
    repo = Path(repo)
    windows = use_windows_scripts(repo) if windows is None else windows
    pull = ["git", "-C", str(repo), "pull", "--ff-only", REMOTE, BRANCH]
    if windows:
        ps = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
        start = ps + [str(repo / "start.ps1"), "-NoBrowser"] + ([] if with_sampler else ["-NoSampler"])
        return [("stop", ps + [str(repo / "stop.ps1")]), ("pull", pull),
                ("install", ps + [str(repo / "install.ps1")]), ("start", start)]
    py = python or sys.executable
    svc = [py, str(repo / "service.py")]
    common = (["--config", str(config)] if config else []) + ["--data-dir", str(data_dir)]
    install = [py, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(repo / "requirements.txt")]
    return [("stop", svc + ["stop"] + common), ("pull", pull),
            ("install", install if requirements_changed else None),
            ("start", svc + ["start"] + common + ([] if with_sampler else ["--no-sampler"]))]


def _open_log(data_dir):
    p = Path(data_dir) / LOG_NAME
    try:
        if p.exists() and p.stat().st_size > LOG_MAX_BYTES:
            os.replace(p, p.with_name(p.name + ".1"))
    except OSError:
        pass
    return open(p, "a", encoding="utf-8")


def _run_step(argv, timeout, say, cwd):
    say("$ " + " ".join(argv))
    try:
        r = subprocess.run(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, env=dict(_git_env(), PYTHONUTF8="1", PYTHONIOENCODING="utf-8"), **_no_window())
    except subprocess.TimeoutExpired:
        say(f"  timed out after {timeout}s")
        return False
    except OSError as exc:
        say(f"  could not run: {exc}")
        return False
    text = r.stdout.decode("utf-8", "replace").strip()
    for ln in text.splitlines()[-40:]:
        say("  " + ln)
    say(f"  exit code {r.returncode}")
    return r.returncode == 0


def _read_bytes(p):
    try:
        return Path(p).read_bytes()
    except OSError:
        return None


class _EolOverride:
    """Clear "modified" files whose only difference from HEAD is CRLF/LF, without commits or stashes.

    1. ``git checkout -- <files>`` (enough when the file on disk just has other line endings than the blob).
    2. If git still lists them, mark them ``-text`` in ``.git/info/attributes`` (local to this clone, never committed)
       for the duration of the pull: git then compares raw bytes, sees no change, and ``pull --ff-only`` can run.
       ``git stash`` and ``git reset --keep`` do not help here: with the committed CRLF blob and ``eol=crlf``
       every checkout produces the same "modified" file again.
    The original .git/info/attributes is restored afterwards (``restore``)."""

    MARK = "# self_update: temporary, line endings only"

    def __init__(self, repo, files, say):
        self.repo, self.files, self.say = Path(repo), list(files), say
        self.path, self.before, self.active = None, None, False

    def apply(self):
        say = self.say
        say("line endings only (not real edits): " + ", ".join(self.files))
        try:
            git(self.repo, "checkout", "--", *self.files)
            say("$ git checkout -- " + " ".join(self.files))
        except GitError as exc:
            say(f"  git checkout failed: {exc}")
        real, still = changed_files(self.repo)
        if real:
            say("real local changes found: " + ", ".join(real))
            return False
        if not still:
            say("line-ending differences cleared")
            return True
        try:
            self.path = Path(git(self.repo, "rev-parse", "--git-path", "info/attributes"))
            if not self.path.is_absolute():
                self.path = self.repo / self.path
            self.before = self.path.read_bytes() if self.path.exists() else None
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lines = [self.MARK] + ["/" + f + " -text" for f in still]
            pre = (self.before or b"")
            if pre and not pre.endswith(b"\n"):
                pre += b"\n"
            self.path.write_bytes(pre + ("\n".join(lines) + "\n").encode("utf-8"))
            self.active = True
            say(f"marked {len(still)} file(s) -text in {self.path} for this update")
            git(self.repo, "update-index", "-q", "--refresh")
        except (OSError, GitError) as exc:
            say(f"  could not set the temporary attributes: {exc}")
        real, still = changed_files(self.repo)
        if still or real:
            say("still listed as modified: " + ", ".join(still + real))
        else:
            say("line-ending differences cleared")
        return not real

    def restore(self):
        if not self.active:
            return
        try:
            if self.before is None:
                self.path.unlink()
            else:
                self.path.write_bytes(self.before)
            self.say(f"restored {self.path}")
            git(self.repo, "update-index", "-q", "--refresh")
        except (OSError, GitError) as exc:
            self.say(f"  could not restore {self.path}: {exc}")
        self.active = False


def _wait_gone(pid, timeout, say):
    if not pid:
        return
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if not proc_util.pid_alive(pid):
            say(f"old server (pid {pid}) has exited")
            return
        time.sleep(0.5)
    if proc_util.looks_like_python(pid):  # e.g. a server started by hand, not by service.py
        say(f"old server (pid {pid}) still running; stopping it")
        proc_util.terminate(pid)


def run(repo, data_dir, config=None, wait_pid=None, runner=None, delay=2.0, rollback=False):
    """The detached updater (``rollback=True``: back to the commit in update_prev.json). Returns True when every
    step worked. ``runner(argv, timeout, say, cwd)`` is for tests."""
    repo, data_dir = Path(repo), Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    runner = runner or _run_step
    log = _open_log(data_dir)
    lock = data_dir / LOCK_NAME

    def say(msg):
        log.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S%z')}] {msg}\n"); log.flush()
    lock.write_text(str(os.getpid()), encoding="utf-8")
    ok = True
    try:
        say(f"{'rollback' if rollback else 'update'} started (pid {os.getpid()}, folder {repo})")
        svc = {}
        try:
            svc = json.loads((data_dir / "service.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        with_sampler = "sampler" in svc or not svc  # keep the "--no-sampler" choice of the running service
        old = None
        try:
            old = git(repo, "rev-parse", "HEAD")
        except (GitError, OSError):
            pass
        time.sleep(delay)  # let the page receive its answer first
        steps = dict(plan(repo, data_dir, config, with_sampler))
        if not runner(steps["stop"], STEP_TIMEOUT["stop"], say, repo):
            say("stop reported a problem; continuing")
        _wait_gone(wait_pid, 30, say)
        pre = rollback_check(repo, data_dir) if rollback else check(repo, fetch=False)
        eol = _EolOverride(repo, pre.get("eol_only") or [], say)
        pulled = tried = False
        try:
            if rollback:
                if not pre.get("can_rollback"):
                    say(f"not rolling back: {pre.get('state')} {pre.get('detail') or ''}".rstrip())
                elif eol.files and not eol.apply():
                    say("not rolling back: real local changes")
                else:
                    # stays on main (no detached HEAD); --keep refuses instead of discarding local edits and leaves
                    # untracked / ignored files (usage-data/, usage_config.json) alone; never --hard
                    tried = True
                    say(f"$ git reset --keep {pre['commit']}  (back to {pre.get('version') or pre['short']})")
                    try:
                        git(repo, "reset", "--keep", pre["commit"])
                        pulled = True
                        say("  reset --keep worked")
                        try:
                            (data_dir / PREV_FILE).unlink()
                        except OSError:
                            pass
                    except GitError as exc:
                        say(f"  reset --keep failed: {exc}")
            elif not pre.get("can_update"):
                say(f"not updating: {pre.get('state')} {pre.get('detail') or ''}".rstrip())
            elif eol.files and not eol.apply():
                say("not updating: real local changes")
            elif not _record(repo, data_dir, say):
                say("not updating: could not save the current version for a later rollback")
            elif (tried := True) and runner(steps["pull"], STEP_TIMEOUT["pull"], say, repo):
                pulled = True
            elif eol.files and not changed_files(repo)[0]:
                # pull refused although only line endings differ: move the branch with --keep (keeps any local
                # edit and every untracked / ignored file such as usage_config.json and usage-data/; never --hard)
                say(f"$ git reset --keep {REMOTE}/{BRANCH}  (pull refused; only line endings differ)")
                try:
                    git(repo, "reset", "--keep", f"{REMOTE}/{BRANCH}")
                    pulled = True
                    say("  reset --keep worked")
                except GitError as exc:
                    say(f"  reset --keep failed: {exc}")
        finally:
            eol.restore()
        if not pulled:
            if tried:
                say("ERROR: " + ("git reset --keep" if rollback else "git pull --ff-only") + " failed; starting the current version again")
            ok = False
        else:
            new = None
            try:
                new = git(repo, "rev-parse", "HEAD")
                changed = bool(old and new and git(repo, "diff", "--name-only", old, new, "--", "requirements.txt"))
            except (GitError, OSError):
                changed = True
            say(f"{'rolled back' if rollback else 'updated'} {(old or '?')[:7]} -> {(new or '?')[:7]}")
            install = dict(plan(repo, data_dir, config, with_sampler, requirements_changed=changed))["install"]
            if install is None:
                say("requirements.txt unchanged; skipping pip install")
            else:
                cfg_file = Path(config) if config else repo / "usage_config.json"
                before = _read_bytes(cfg_file)
                if not runner(install, STEP_TIMEOUT["install"], say, repo):
                    say("ERROR: install step failed; starting anyway (see the messages above)")
                    ok = False
                if before is not None and _read_bytes(cfg_file) != before:
                    cfg_file.write_bytes(before)  # an update never changes the user's settings
                    say(f"kept your settings: restored {cfg_file.name}")
        if not runner(steps["start"], STEP_TIMEOUT["start"], say, repo):
            say("ERROR: start failed. Start it by hand (start.ps1 or: python3 service.py start)")
            ok = False
        say(("rollback" if rollback else "update") + (" finished" if ok else " finished with problems"))
    except Exception as exc:  # last resort: log it and try to bring the dashboard back
        ok = False
        say(f"ERROR: {exc!r}")
        try:
            runner(dict(plan(repo, data_dir, config))["start"], STEP_TIMEOUT["start"], say, repo)
        except Exception as exc2:
            say(f"ERROR: start failed too: {exc2!r}")
    finally:
        try:
            if lock.read_text(encoding="utf-8").strip() == str(os.getpid()):
                lock.unlink()
        except OSError:
            pass
        log.close()
    return ok


def _record(repo, data_dir, say):
    try:
        rec = record_previous(repo, data_dir)
        say(f"saved the current version for rollback: {rec['commit'][:7]} {rec.get('version') or ''} -> {PREV_FILE}".rstrip())
        return True
    except (OSError, GitError) as exc:
        say(f"  could not save {PREV_FILE}: {exc}")
        return False


def _detached_flags():
    if os.name != "nt":
        return {"start_new_session": True}
    f = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    return {"creationflags": f}


def _popen_detached(argv, cwd):
    kw = dict(cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              close_fds=True, env=dict(os.environ, PYTHONUTF8="1"), **_detached_flags())
    if os.name == "nt":  # leave a Task Scheduler job if allowed, so ending the old service cannot end us
        try:
            return subprocess.Popen(argv, **dict(kw, creationflags=kw["creationflags"] | 0x01000000))
        except OSError:
            pass
    return subprocess.Popen(argv, **kw)


def spawn(repo, data_dir, config=None, wait_pid=None, python=None, rollback=False):
    """Start the updater through a short-lived launcher, so it is no child of the server: ``stop`` on
    Windows ends the whole process tree (taskkill /T) and must not take the updater with it."""
    args = ["--repo", str(repo), "--data-dir", str(data_dir)] + (["--config", str(config)] if config else []) \
        + (["--wait-pid", str(wait_pid)] if wait_pid else []) + (["--rollback"] if rollback else [])
    p = _popen_detached([python or sys.executable, str(Path(__file__).resolve()), "launch"] + args, repo)
    try:
        p.wait(timeout=20)
    except subprocess.TimeoutExpired:
        pass
    return p.returncode == 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Check for a newer version / update in place (fast-forward only).")
    p.add_argument("cmd", choices=["check", "run", "launch", "previous"])
    p.add_argument("--rollback", action="store_true", help="run: go back to the version saved before the last update")
    p.add_argument("--repo", default=str(HERE))
    p.add_argument("--data-dir", dest="data_dir")
    p.add_argument("--config")
    p.add_argument("--wait-pid", dest="wait_pid", type=int)
    p.add_argument("--no-fetch", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "check":
        print(json.dumps(check(a.repo, fetch=not a.no_fetch), ensure_ascii=False))
        return
    if not a.data_dir:
        p.error("--data-dir is required")
    if a.cmd == "previous":
        print(json.dumps(rollback_check(a.repo, a.data_dir), ensure_ascii=False))
        return
    rest = ["--repo", a.repo, "--data-dir", a.data_dir] + (["--config", a.config] if a.config else []) \
        + (["--wait-pid", str(a.wait_pid)] if a.wait_pid else []) + (["--rollback"] if a.rollback else [])
    if a.cmd == "launch":
        _popen_detached([sys.executable, str(Path(__file__).resolve()), "run"] + rest, a.repo)
        return
    sys.exit(0 if run(a.repo, a.data_dir, a.config, a.wait_pid, rollback=a.rollback) else 1)


if __name__ == "__main__":
    main()
