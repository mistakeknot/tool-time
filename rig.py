#!/usr/bin/env python3
"""tool-time rig auditor.

Collects the raw facts needed to audit a rig: where each MCP server is
registered (global config, per-project config, or plugin), how often it
was actually called, and what it costs in resident memory right now.
Writes rig.json. No opinions, no thresholds — just data for an agent
to reason about.

Every input path accepts an env override so tests can inject fixtures:
TOOL_TIME_DATA_DIR, TOOL_TIME_CLAUDE_JSON, TOOL_TIME_SETTINGS_JSON,
TOOL_TIME_PLUGIN_CACHE, TOOL_TIME_PS_CMD.
"""

import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

WINDOW_DAYS = [30, 90]

# Launcher/interpreter tokens too generic to identify a server process
GENERIC_TOKENS = {
    "node", "npx", "npm", "pnpm", "yarn", "bun", "deno",
    "python", "python3", "uv", "uvx", "tsx", "sh", "bash", "env",
}

# Credential redaction — rig.json ships into session transcripts via the
# rig-audit skill, so inline secrets must never survive command_string().
REDACTED = "<redacted>"
SENSITIVE_ARG_RE = re.compile(r"key|token|secret|pass|auth", re.IGNORECASE)
HIGH_ENTROPY_RE = re.compile(r"^[A-Za-z0-9_-]{32,}$")


def _env_path(var: str, default: Path) -> Path:
    """Resolve an input path, honoring the env override if set."""
    value = os.environ.get(var)
    return Path(value) if value else default


def parse_mcp_tool(tool: str) -> tuple[str, str, str | None, str | None] | None:
    """Parse mcp__<server>__<tool> into (qualified, name, source, alias).

    Plugin-prefixed segments like "plugin_interknow_qmd" become the
    source-qualified identity "plugin:interknow/qmd" with name "qmd" and
    source "plugin:interknow"; the raw segment is kept as an alias.
    Plain segments stay bare: qualified equals the name, no source/alias.
    Same-named servers from different sources are never merged here —
    that relationship is only hypothesized in the "duplicates" section.
    """
    if not tool.startswith("mcp__"):
        return None
    parts = tool.split("__", 2)
    if len(parts) < 3 or not parts[1]:
        return None
    segment = parts[1]
    if segment.startswith("plugin_"):
        rest = segment[len("plugin_"):]
        if "_" in rest:
            plugin, server = rest.split("_", 1)
            if plugin and server:
                return f"plugin:{plugin}/{server}", server, f"plugin:{plugin}", segment
    return segment, segment, None, None


def collect_usage(
    events_file: Path,
    now: datetime,
    warnings: list[str],
) -> tuple[dict[str, dict[str, Any]], list[str], int]:
    """Scan events.jsonl for MCP server usage and skill invocations.

    Returns (usage, skill_events_30d, events_scanned) where usage maps
    the source-qualified server identity (e.g. "plugin:interknow/qmd",
    or a bare name for non-plugin servers) to working counts and
    skill_events_30d holds raw "skill" field values from the 30-day
    window. Calls are counted at PostToolUse (matching analyze.py) to
    avoid double-counting.
    """
    usage: dict[str, dict[str, Any]] = {}
    skill_events_30d: list[str] = []
    events_scanned = 0
    if not events_file.exists():
        warnings.append(f"events file not found: {events_file}")
        return usage, skill_events_30d, events_scanned
    try:
        lines = events_file.read_text().splitlines()
    except OSError as e:
        warnings.append(f"events file unreadable: {e}")
        return usage, skill_events_30d, events_scanned

    cutoff_30 = now - timedelta(days=30)
    cutoff_90 = now - timedelta(days=90)
    for line in lines:
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
            ts = datetime.fromisoformat(ev["ts"].replace("Z", "+00:00"))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        events_scanned += 1
        if ev.get("event") not in ("PostToolUse", "ToolUse"):
            continue
        tool = ev.get("tool") or ""

        # Skill usage — the skill field is best-effort (historical events
        # sometimes carry file paths here), so keep raw values for the
        # plugin section to substring-match against.
        skill = ev.get("skill")
        if ts >= cutoff_30 and (skill or tool == "Skill"):
            skill_events_30d.append(str(skill or ""))

        parsed = parse_mcp_tool(tool)
        if parsed is None:
            continue
        qualified, server_name, plugin_source, alias = parsed
        entry = usage.setdefault(qualified, {
            "name": server_name,
            "calls_30d": 0,
            "calls_90d": 0,
            "calls_total": 0,
            "last_used": None,
            "sources": set(),
            "aliases": set(),
        })
        entry["calls_total"] += 1
        if ts >= cutoff_90:
            entry["calls_90d"] += 1
        if ts >= cutoff_30:
            entry["calls_30d"] += 1
        if entry["last_used"] is None or ts > entry["last_used"]:
            entry["last_used"] = ts
        if plugin_source:
            entry["sources"].add(plugin_source)
        if alias:
            entry["aliases"].add(alias)
    return usage, skill_events_30d, events_scanned


def read_json_object(path: Path, label: str, warnings: list[str]) -> dict[str, Any]:
    """Read a JSON object, degrading to {} with a warning on any failure."""
    if not path.exists():
        warnings.append(f"{label} not found: {path}")
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        warnings.append(f"{label} unreadable: {e}")
        return {}
    if not isinstance(data, dict):
        warnings.append(f"{label} is not a JSON object: {path}")
        return {}
    return data


def _redact_url(token: str) -> str:
    """Strip the query string, fragment, and userinfo from a URL."""
    scheme, _, rest = token.partition("://")
    rest = rest.split("?", 1)[0].split("#", 1)[0]
    authority, slash, path = rest.partition("/")
    if "@" in authority:
        authority = authority.rsplit("@", 1)[-1]
    return scheme + "://" + authority + slash + path


def redact_tokens(tokens: list[str]) -> list[str]:
    """Redact credential-looking material from a command token list.

    - The token following any arg matching key/token/secret/pass/auth
      (case-insensitive) becomes "<redacted>", as does the value side of
      an inline --api-key=... form.
    - Bare high-entropy tokens (32+ chars of [A-Za-z0-9_-], which by
      construction excludes path-like values) become "<redacted>".
    - URLs lose their query string, fragment, and userinfo.
    """
    out: list[str] = []
    redact_next = False
    for token in tokens:
        # A flag is never the "value" of the preceding sensitive arg —
        # it falls through and is re-evaluated itself (it may set
        # redact_next again), so "secretive-mcp --api-key VALUE" still
        # redacts VALUE rather than the flag.
        if redact_next and not token.startswith("-"):
            out.append(REDACTED)
            redact_next = False
            continue
        redact_next = False
        if "://" in token:
            out.append(_redact_url(token))
            continue
        key, sep, _value = token.partition("=")
        if sep and SENSITIVE_ARG_RE.search(key):
            out.append(key + "=" + REDACTED)
            continue
        if SENSITIVE_ARG_RE.search(token):
            out.append(token)
            redact_next = True
            continue
        if HIGH_ENTROPY_RE.match(token):
            out.append(REDACTED)
            continue
        out.append(token)
    return out


def command_string(cfg: Any, plugin: str = "") -> str:
    """Flatten a server config into a normalized, credential-redacted string.

    ${CLAUDE_PLUGIN_ROOT} becomes a per-plugin placeholder ("<root:plugin>",
    or "<root>" when no plugin is given) so two plugins' identical relative
    layouts (e.g. "node ${CLAUDE_PLUGIN_ROOT}/dist/index.js") never compare
    equal, while the same server registered twice still matches.
    """
    if not isinstance(cfg, dict):
        return ""
    cmd = cfg.get("command") or cfg.get("url") or ""
    args = cfg.get("args") or []
    if not isinstance(args, list):
        args = [args]
    placeholder = f"<root:{plugin}>" if plugin else "<root>"
    parts: list[str] = []
    for raw in [cmd, *args]:
        token = str(raw)
        for var in ("${CLAUDE_PLUGIN_ROOT}", "$CLAUDE_PLUGIN_ROOT"):
            token = token.replace(var, placeholder)
        parts.extend(token.split())
    return " ".join(redact_tokens(parts))


def distinctive_token(cfg: Any, plugin_root: str = "") -> str:
    """Pick the config token most likely to appear in the server's process args.

    Skips generic launchers (node, npx, ...) and flags; the first
    remaining token is usually the executable path or package name.
    Not redacted — this never ships in rig.json, it only drives local
    ps matching.
    """
    if not isinstance(cfg, dict):
        return ""
    cmd = cfg.get("command") or cfg.get("url") or ""
    args = cfg.get("args") or []
    if not isinstance(args, list):
        args = [args]
    for raw in [cmd, *args]:
        token = str(raw)
        # ${CLAUDE_PLUGIN_ROOT} won't appear in expanded process args;
        # resolve it to the plugin's cache dir when known, else match on
        # the path relative to the plugin root
        for var in ("${CLAUDE_PLUGIN_ROOT}", "$CLAUDE_PLUGIN_ROOT"):
            if token.startswith(var):
                rest = token[len(var):].lstrip("/")
                token = f"{plugin_root.rstrip('/')}/{rest}" if plugin_root else rest
        if not token:
            continue
        if token.startswith("--package="):
            return token.split("=", 1)[1]
        if token.startswith("-"):
            continue
        if token in GENERIC_TOKENS or os.path.basename(token) in GENERIC_TOKENS:
            continue
        return token
    return str(cmd)


def token_matches_process(token: str, proc_args: str) -> bool:
    """True if a config token identifies this process on a token boundary.

    Splits the ps args into whitespace tokens and compares exact tokens,
    path suffixes (for path-like config tokens), and path basenames (for
    bare ones) — never raw substring containment, so server "qmd" does
    not absorb processes of "someother_qmd".
    """
    if not token:
        return False
    for arg in proc_args.split():
        if arg == token:
            return True
        if "/" in token:
            if arg.endswith("/" + token):
                return True
        elif os.path.basename(arg) == token:
            return True
    return False


def extract_server_map(data: Any) -> dict[str, Any]:
    """Accept either {"mcpServers": {...}} or a bare name->config map."""
    if not isinstance(data, dict):
        return {}
    inner = data.get("mcpServers")
    if isinstance(inner, dict):
        return inner
    return {
        name: cfg for name, cfg in data.items()
        if isinstance(cfg, dict) and ("command" in cfg or "url" in cfg)
    }


def collect_registrations(
    claude_json_file: Path,
    settings_file: Path,
    cache_dir: Path,
    warnings: list[str],
) -> tuple[list[dict[str, str]], list[str], dict[str, int]]:
    """Gather every place a server is registered: global, project, plugin.

    Returns (registrations, enabled_plugins, skills_shipped). Each
    registration is {"name", "source", "command", "token"}.
    """
    registrations: list[dict[str, str]] = []

    def add(
        name: str, source: str, cfg: Any, plugin: str = "", plugin_root: str = "",
    ) -> None:
        registrations.append({
            "name": name,
            "source": source,
            "command": command_string(cfg, plugin=plugin),
            "token": distinctive_token(cfg, plugin_root=plugin_root),
        })

    # Global and per-project servers from ~/.claude.json
    config = read_json_object(claude_json_file, "claude.json", warnings)
    servers = config.get("mcpServers")
    if isinstance(servers, dict):
        for name, cfg in servers.items():
            add(name, "global", cfg)
    projects = config.get("projects")
    if isinstance(projects, dict):
        for proj_path, proj in projects.items():
            proj_servers = proj.get("mcpServers") if isinstance(proj, dict) else None
            if isinstance(proj_servers, dict):
                for name, cfg in proj_servers.items():
                    add(name, f"project:{proj_path}", cfg)

    # Plugin servers — enabled plugins only, scanned from the cache dir
    settings = read_json_object(settings_file, "settings.json", warnings)
    enabled_raw = settings.get("enabledPlugins")
    enabled: list[str] = []
    if isinstance(enabled_raw, dict):
        enabled = sorted({key.split("@")[0] for key, on in enabled_raw.items() if on})
    skills_shipped: dict[str, int] = {}
    if not cache_dir.is_dir():
        warnings.append(f"plugin cache not found: {cache_dir}")
    for plugin in enabled:
        plugin_servers: dict[str, tuple[Any, str]] = {}
        shipped = 0
        # cache/<marketplace>/<plugin>/<version>/ — later versions win
        version_dirs = sorted(cache_dir.glob(f"*/{plugin}/*")) if cache_dir.is_dir() else []
        for version_dir in version_dirs:
            if not version_dir.is_dir():
                continue
            for candidate in (
                version_dir / ".claude-plugin" / "plugin.json",
                version_dir / "plugin.json",
            ):
                if not candidate.is_file():
                    continue
                try:
                    manifest = json.loads(candidate.read_text())
                except (json.JSONDecodeError, OSError) as e:
                    warnings.append(f"plugin manifest unreadable: {candidate}: {e}")
                    continue
                if isinstance(manifest, dict):
                    skills = manifest.get("skills")
                    if isinstance(skills, list):
                        shipped = len(skills)
                    mcp = manifest.get("mcpServers")
                    if isinstance(mcp, dict):
                        for name, cfg in mcp.items():
                            plugin_servers[name] = (cfg, str(version_dir))
                break
            mcp_file = version_dir / ".mcp.json"
            if mcp_file.is_file():
                try:
                    extracted = extract_server_map(json.loads(mcp_file.read_text()))
                except (json.JSONDecodeError, OSError) as e:
                    warnings.append(f".mcp.json unreadable: {mcp_file}: {e}")
                else:
                    for name, cfg in extracted.items():
                        plugin_servers[name] = (cfg, str(version_dir))
        for name, (cfg, root) in plugin_servers.items():
            add(name, f"plugin:{plugin}", cfg, plugin=plugin, plugin_root=root)
        skills_shipped[plugin] = shipped
    return registrations, enabled, skills_shipped


def collect_processes(ps_cmd: str, warnings: list[str]) -> list[tuple[int, str]]:
    """Run ps (or the test override) and parse (rss_kb, args) rows."""
    try:
        result = subprocess.run(
            ps_cmd, shell=True, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as e:
        warnings.append(f"ps failed: {e}")
        return []
    if result.returncode != 0:
        warnings.append(f"ps exited {result.returncode}: {result.stderr.strip()[:200]}")
        return []
    rows: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            rss_kb = int(parts[0])
        except ValueError:
            continue  # header line
        rows.append((rss_kb, parts[1]))
    return rows


def registration_key(reg: dict[str, str]) -> str:
    """Source-qualified identity for a registration.

    Plugin registrations key as "plugin:<plugin>/<name>" so same-named
    servers from different plugins never merge; global and per-project
    registrations share the bare name (events cannot distinguish them).
    """
    if reg["source"].startswith("plugin:"):
        return f"{reg['source']}/{reg['name']}"
    return reg["name"]


def find_duplicates(registrations: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Find servers possibly reachable via 2+ registrations.

    Groups by server name (reason "name_match" — a hypothesis: same-named
    registrations from different sources may or may not be the same
    server), then by normalized command string (reason "command_match" —
    the same launch command registered twice). Usage counts are never
    merged on these hypotheses; this section only surfaces them.
    Command groups fully covered by a name group are not re-reported.
    """
    def entry(canonical: str, reason: str, idxs: list[int]) -> dict[str, Any]:
        return {
            "canonical": canonical,
            "reason": reason,
            "registrations": [
                {
                    "name": registrations[i]["name"],
                    "source": registrations[i]["source"],
                    "command": registrations[i]["command"],
                }
                for i in idxs
            ],
        }

    duplicates: list[dict[str, Any]] = []
    emitted: list[set[int]] = []
    by_name: dict[str, list[int]] = defaultdict(list)
    for i, reg in enumerate(registrations):
        by_name[reg["name"]].append(i)
    for name in sorted(by_name):
        idxs = by_name[name]
        if len(idxs) >= 2:
            duplicates.append(entry(name, "name_match", idxs))
            emitted.append(set(idxs))

    by_command: dict[str, list[int]] = defaultdict(list)
    for i, reg in enumerate(registrations):
        if reg["command"]:
            by_command[reg["command"]].append(i)
    for command in sorted(by_command):
        idxs = by_command[command]
        if len(idxs) < 2:
            continue
        idx_set = set(idxs)
        if any(idx_set <= prev for prev in emitted):
            continue
        canonical = min(registrations[i]["name"] for i in idxs)
        duplicates.append(entry(canonical, "command_match", idxs))
        emitted.append(idx_set)
    return duplicates


def build_rig(
    usage: dict[str, dict[str, Any]],
    registrations: list[dict[str, str]],
    enabled_plugins: list[str],
    skills_shipped: dict[str, int],
    processes: list[tuple[int, str]],
    skill_events_30d: list[str],
    events_scanned: int,
    warnings: list[str],
    now: datetime,
) -> dict[str, Any]:
    """Assemble the rig.json document from the collected pieces."""
    # Merge usage and registrations under source-qualified identities —
    # same-named servers from different plugins stay separate entries
    keys = set(usage) | {registration_key(reg) for reg in registrations}
    tokens_by_server: dict[str, set[str]] = defaultdict(set)
    names_by_key: dict[str, str] = {}
    for reg in registrations:
        reg_key = registration_key(reg)
        names_by_key.setdefault(reg_key, reg["name"])
        if reg["token"]:
            tokens_by_server[reg_key].add(reg["token"])
    for used_key, used in usage.items():
        names_by_key.setdefault(used_key, used.get("name", used_key))

    servers: dict[str, dict[str, Any]] = {}
    for key in sorted(
        keys,
        key=lambda k: (-usage.get(k, {}).get("calls_30d", 0), k),
    ):
        used = usage.get(key, {})
        sources = set(used.get("sources", set()))
        commands: set[str] = set()
        for reg in registrations:
            if registration_key(reg) == key:
                sources.add(reg["source"])
                if reg["command"]:
                    commands.add(reg["command"])
        proc_count = 0
        rss_kb = 0
        tokens = tokens_by_server.get(key, set())
        for kb, args in processes:
            if any(token_matches_process(token, args) for token in tokens):
                proc_count += 1
                rss_kb += kb
        last_used = used.get("last_used")
        servers[key] = {
            "name": names_by_key.get(key, key),
            "calls_30d": used.get("calls_30d", 0),
            "calls_90d": used.get("calls_90d", 0),
            "calls_total": used.get("calls_total", 0),
            "last_used": (
                last_used.strftime("%Y-%m-%dT%H:%M:%SZ") if last_used else None
            ),
            "sources": sorted(sources),
            "aliases": sorted(used.get("aliases", set())),
            "commands": sorted(commands),
            "proc_count": proc_count,
            "rss_mb": round(rss_kb / 1024, 1),
        }

    registered_keys = {registration_key(reg) for reg in registrations}
    zero_use = sorted(
        key for key in registered_keys if servers[key]["calls_30d"] == 0
    )

    plugins: dict[str, dict[str, int]] = {}
    for plugin in enabled_plugins:
        prefix = f"plugin:{plugin}/"
        plugins[plugin] = {
            "mcp_calls_30d": sum(
                s["calls_30d"] for key, s in servers.items()
                if key.startswith(prefix)
            ),
            "skill_calls_30d": sum(
                1 for value in skill_events_30d if plugin in value
            ),
            "skills_shipped": skills_shipped.get(plugin, 0),
        }

    return {
        "servers": servers,
        "duplicates": find_duplicates(registrations),
        "zero_use": zero_use,
        "plugins": plugins,
        "meta": {
            "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window_days": WINDOW_DAYS,
            "events_scanned": events_scanned,
            "warnings": warnings,
        },
    }


def main() -> None:
    warnings: list[str] = []
    now = datetime.now(timezone.utc)
    data_dir = _env_path("TOOL_TIME_DATA_DIR", Path.home() / ".claude" / "tool-time")
    claude_json_file = _env_path("TOOL_TIME_CLAUDE_JSON", Path.home() / ".claude.json")
    settings_file = _env_path(
        "TOOL_TIME_SETTINGS_JSON", Path.home() / ".claude" / "settings.json"
    )
    cache_dir = _env_path(
        "TOOL_TIME_PLUGIN_CACHE", Path.home() / ".claude" / "plugins" / "cache"
    )
    ps_cmd = os.environ.get("TOOL_TIME_PS_CMD") or "ps -axo rss,args"

    usage, skill_events_30d, events_scanned = collect_usage(
        data_dir / "events.jsonl", now, warnings
    )
    registrations, enabled_plugins, skills_shipped = collect_registrations(
        claude_json_file, settings_file, cache_dir, warnings
    )
    processes = collect_processes(ps_cmd, warnings)

    rig = build_rig(
        usage, registrations, enabled_plugins, skills_shipped,
        processes, skill_events_30d, events_scanned, warnings, now,
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    rig_file = data_dir / "rig.json"
    rig_file.write_text(json.dumps(rig, indent=2) + "\n")
    print(str(rig_file))


if __name__ == "__main__":
    main()
