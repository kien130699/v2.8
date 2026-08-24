from __future__ import annotations

import os
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Mapping

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
_LOCK = RLock()
_SOURCES: dict[str, str] = {}

# These are read-only fallbacks. V2.8 never writes to old V2.4/V2.5/V2.6 trees.
_LOCAL_ENV_CANDIDATES = [
    ENV_PATH,
    ROOT / "modules" / "facebook" / ".env",
    ROOT / "modules" / "facebook" / "engine_v27" / ".env",
    ROOT / "modules" / "flow_content" / ".env",
    ROOT / "modules" / "parenting" / ".env",
]

# Keys that existed in the three source factories and are safe to discover read-only
# when consolidating them into V2.8. Secret values are never logged/returned.
_DISCOVER_KEYS = {
    "PEXELS_API_KEY", "PIXABAY_API_KEY", "SERPER_API_KEY",
    "9ROUTER_API_KEY", "9ROUTER_BASE_URL", "9ROUTER_DEFAULT_MODEL",
}


def _nonempty(value: Any) -> bool:
    return value is not None and bool(str(value).strip())


def _clean_key(raw_key: Any) -> str:
    return str(raw_key or "").lstrip("\ufeff").strip()


def _parse_env(path: Path) -> dict[str, str]:
    try:
        if not path.is_file():
            return {}
        raw = dotenv_values(path, encoding="utf-8-sig")
    except Exception:
        return {}
    out: dict[str, str] = {}
    for raw_key, raw_value in raw.items():
        key = _clean_key(raw_key)
        if key and _nonempty(raw_value):
            out[key] = str(raw_value).strip()
    return out


def _explicit_env_file() -> Path | None:
    raw = str(os.environ.get("V28_ENV_FILE") or "").strip().strip('"')
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except Exception:
        return Path(raw)


def _legacy_env_candidates() -> list[Path]:
    """Discover old V2.4/V2.5/V2.6 .env files read-only.

    The user's layout is D:\\YT\\Code\\V2.8_Facebook_Job_Factory with sibling
    V2.4/V2.5/V2.6 trees. We never bind their ports, touch their extensions, or
    modify their files. Discovery is capped to avoid an expensive recursive scan.
    """
    parent = ROOT.parent
    found: list[Path] = []
    try:
        for pattern in ("V2.4*/**/.env", "V2.5*/**/.env", "V2.6*/**/.env"):
            for p in parent.glob(pattern):
                if p.is_file() and p not in found:
                    found.append(p)
                    if len(found) >= 40:
                        return found
    except Exception:
        pass
    return found


def env_candidates(include_legacy: bool = True) -> list[Path]:
    result: list[Path] = []
    explicit = _explicit_env_file()
    if explicit:
        result.append(explicit)
    for p in _LOCAL_ENV_CANDIDATES:
        if p not in result:
            result.append(p)
    if include_legacy and str(os.environ.get("V28_DISCOVER_LEGACY_ENV", "1")).strip().lower() not in {"0", "false", "no", "off"}:
        for p in _legacy_env_candidates():
            if p not in result:
                result.append(p)
    return result


def _resolve_key(key: str) -> tuple[str, str]:
    """Resolve one key without trusting mutable os.environ as the only source.

    This is intentionally a pure-ish lookup used by preflight, health and child
    subprocess construction. It prevents split-brain cases where diagnostics see a
    key in a file but a worker later observes an empty/stale process variable.
    """
    key = _clean_key(key)
    if not key:
        return "", "unset"

    process_value = os.environ.get(key)
    source_hint = _SOURCES.get(key, "")
    # A genuine non-empty parent/process value wins. Values previously copied from a
    # file by this resolver are refreshed from disk below so editing .env takes effect.
    if _nonempty(process_value) and not source_hint.startswith("file:"):
        return str(process_value).strip(), "process"

    # Deterministic local precedence.
    for path in env_candidates(include_legacy=False):
        value = _parse_env(path).get(key)
        if _nonempty(value):
            return str(value).strip(), f"file:{path}"

    # Shared consolidation keys may live in the old V2.4/V2.5/V2.6 trees. Read-only.
    if key in _DISCOVER_KEYS and str(os.environ.get("V28_DISCOVER_LEGACY_ENV", "1")).strip().lower() not in {"0", "false", "no", "off"}:
        for path in _legacy_env_candidates():
            value = _parse_env(path).get(key)
            if _nonempty(value):
                return str(value).strip(), f"file:{path}"

    # Last-resort non-empty process value, including one previously loaded from a file
    # whose file has since disappeared. Better to keep a valid secret than report MISS.
    if _nonempty(process_value):
        return str(process_value).strip(), source_hint or "process"
    return "", "process-empty" if process_value is not None else "unset"


def resolve_env_snapshot(keys: Iterable[str] | None = None) -> dict[str, str]:
    """Return resolved values directly from the source-of-truth chain.

    Unlike callers reading os.environ themselves, this function cannot disagree with
    env_status(). Secrets are returned only internally; user-facing diagnostics never
    expose values.
    """
    with _LOCK:
        requested = {_clean_key(k) for k in keys} if keys is not None else set(_DISCOVER_KEYS)
        result: dict[str, str] = {}
        for key in requested:
            if not key:
                continue
            value, source = _resolve_key(key)
            _SOURCES[key] = source
            if _nonempty(value):
                result[key] = value
                # Keep legacy imported modules consistent too.
                os.environ[key] = value
            else:
                result[key] = ""
        return result


def load_project_env(keys: Iterable[str] | None = None) -> dict[str, str]:
    """Ensure process env is loaded from authoritative files once per process."""
    import sys
    if sys.platform == "win32":
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if keys is None:
        # Load every key present in local env files plus shared legacy/discovery keys.
        all_keys: set[str] = set(_DISCOVER_KEYS)
        for path in env_candidates(include_legacy=False):
            all_keys.update(_parse_env(path))
        resolve_env_snapshot(all_keys)
    else:
        resolve_env_snapshot(keys)
    return dict(_SOURCES)


def get_env(key: str, default: str = "") -> str:
    value = resolve_env_snapshot([key]).get(_clean_key(key), "")
    return value if _nonempty(value) else default


def build_subprocess_env(extra: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Return an explicit child env using the same resolver as Job 1 preflight."""
    # First copy ordinary process variables, then explicitly overlay every shared secret
    # from the resolver. This guarantees child BROLL and preflight cannot disagree.
    load_project_env()
    env = {str(k): str(v) for k, v in os.environ.items()}
    snapshot = resolve_env_snapshot(_DISCOVER_KEYS)
    for key, value in snapshot.items():
        if _nonempty(value):
            env[key] = value
        else:
            env.pop(key, None)
    # Windows-safe Unicode contract for every V2.8 child Python process.
    # Override inherited ANSI/charmap settings: one Vietnamese log line must never kill a render.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONLEGACYWINDOWSSTDIO"] = "0"
    if extra:
        for k, v in extra.items():
            if v is not None:
                env[str(k)] = str(v)
    return env


def env_status(*keys: str) -> dict[str, dict[str, Any]]:
    """Safe diagnostics derived from the exact same resolver as preflight."""
    snapshot = resolve_env_snapshot(keys)
    result: dict[str, dict[str, Any]] = {}
    for raw_key in keys:
        key = _clean_key(raw_key)
        value = snapshot.get(key, "")
        source = _SOURCES.get(key, "unset")
        item: dict[str, Any] = {"configured": _nonempty(value), "source": source}
        if source.startswith("file:"):
            item["source_path"] = source[5:]
            item["source"] = "file"
        result[key] = item
    return result


def env_file_info() -> dict[str, Any]:
    candidates = env_candidates(include_legacy=False)
    return {
        "path": str(ENV_PATH),
        "exists": ENV_PATH.is_file(),
        "candidate_paths": [str(p) for p in candidates if p.is_file()],
        "legacy_discovery": str(os.environ.get("V28_DISCOVER_LEGACY_ENV", "1")).strip().lower() not in {"0", "false", "no", "off"},
    }
