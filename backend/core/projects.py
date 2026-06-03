"""Project persistence.

A project is **one novel**.  It owns novel-level shared state — the StoryBible
and the asset library (@characters / #scenes / $props) — and contains an ordered
list of **episodes** (集).  Each episode flows through:
  imported -> analyzed (contributes to shared StoryBible) -> decomposed (shots).

Sharing the bible + assets at the novel level lets a character introduced in
episode 1 be referenced normally in episode 5 (cross-episode 跨集承接).

Each project lives under data/projects/<id>/project.json; a lightweight index
lists them for the UI.

Legacy projects (pre-episode, with top-level segments/shots) are migrated on
read into a single "第1集" episode so no data is lost.
"""

import time
import uuid
from typing import Any, Optional

from .paths import PROJECTS_DIR
from .store import read_json, write_json

INDEX_FILE = PROJECTS_DIR / "index.json"

# fields that used to live at the project root and now belong to an episode
_EPISODE_FIELDS = ("source_type", "raw_text", "char_count",
                   "segments", "blocks", "shots", "stage")


def _project_path(pid: str):
    return PROJECTS_DIR / pid / "project.json"


def _load_index() -> list:
    return read_json(INDEX_FILE, [])


def _save_index(idx: list) -> None:
    write_json(INDEX_FILE, idx)


def _new_episode(idx: int, name: str = "", *, source_type: str = "txt",
                 raw_text: str = "", parsed: Optional[dict] = None) -> dict:
    parsed = parsed or {}
    now = time.time()
    return {
        "id": uuid.uuid4().hex[:8],
        "idx": idx,
        "name": name or f"第{idx}集",
        "source_type": source_type,
        "raw_text": raw_text,
        "char_count": parsed.get("char_count", len(raw_text)),
        "segments": parsed.get("segments", []),
        "blocks": [],
        "shots": [],
        "stage": "imported",
        "created_at": now,
        "updated_at": now,
    }


def _migrate(project: dict) -> tuple[dict, bool]:
    """Wrap a legacy single-level project into an episodes[] structure."""
    if "episodes" in project and isinstance(project["episodes"], list):
        return project, False
    ep = {
        "id": uuid.uuid4().hex[:8],
        "idx": 1,
        "name": "第1集",
        "source_type": project.get("source_type", "txt"),
        "raw_text": project.get("raw_text", ""),
        "char_count": project.get("char_count", 0),
        "segments": project.get("segments", []),
        "blocks": project.get("blocks", []),
        "shots": project.get("shots", []),
        "stage": project.get("stage", "imported"),
        "created_at": project.get("created_at", time.time()),
        "updated_at": project.get("updated_at", time.time()),
    }
    project["episodes"] = [ep]
    for f in _EPISODE_FIELDS:
        project.pop(f, None)
    return project, True


def _episode_summary(ep: dict) -> dict:
    return {
        "id": ep["id"],
        "idx": ep.get("idx"),
        "name": ep.get("name", ""),
        "stage": ep.get("stage", "imported"),
        "segment_count": len(ep.get("segments", [])),
        "shot_count": len(ep.get("shots", [])),
        "char_count": ep.get("char_count", 0),
    }


def _summarize(p: dict) -> dict:
    eps = p.get("episodes", [])
    return {
        "id": p["id"],
        "name": p.get("name", ""),
        "episode_count": len(eps),
        "segment_count": sum(len(e.get("segments", [])) for e in eps),
        "char_count": sum(e.get("char_count", 0) for e in eps),
        "shot_count": sum(len(e.get("shots", [])) for e in eps),
        "created_at": p.get("created_at"),
        "updated_at": p.get("updated_at"),
    }


def _write(project: dict) -> dict:
    project["updated_at"] = time.time()
    write_json(_project_path(project["id"]), project)
    idx = [_summarize(project) if p["id"] == project["id"] else p
           for p in _load_index()]
    if not any(p["id"] == project["id"] for p in idx):
        idx.append(_summarize(project))
    _save_index(idx)
    return project


def list_projects() -> list:
    idx = _load_index()
    # self-heal: re-summarize any pre-episode index entries (migrates on read)
    if any("episode_count" not in e for e in idx):
        for e in list(idx):
            if "episode_count" not in e:
                get_project(e["id"])  # triggers migrate + reindex
        idx = _load_index()
    return sorted(idx, key=lambda x: x.get("updated_at", 0), reverse=True)


def get_project(pid: str) -> Optional[dict]:
    project = read_json(_project_path(pid), None)
    if project is None:
        return None
    project, changed = _migrate(project)
    if changed:
        write_json(_project_path(pid), project)
        _save_index([_summarize(project) if p["id"] == pid else p
                     for p in _load_index()])
    return project


def create_project(name: str, source_type: str, raw_text: str, parsed: dict) -> dict:
    pid = uuid.uuid4().hex[:12]
    now = time.time()
    ep = _new_episode(1, source_type=source_type, raw_text=raw_text, parsed=parsed)
    project = {
        "id": pid,
        "name": name or f"小说 {time.strftime('%m-%d %H:%M')}",
        "story_bible": None,      # novel-level, shared across episodes
        "assets": [],             # novel-level @/#/$ asset library
        "episodes": [ep],
        "created_at": now,
        "updated_at": now,
    }
    write_json(_project_path(pid), project)
    idx = _load_index()
    idx.append(_summarize(project))
    _save_index(idx)
    return project


def update_project(pid: str, patch: dict[str, Any]) -> Optional[dict]:
    project = get_project(pid)
    if not project:
        return None
    project.update(patch)
    return _write(project)


def delete_project(pid: str) -> None:
    idx = [p for p in _load_index() if p["id"] != pid]
    _save_index(idx)
    path = _project_path(pid)
    if path.exists():
        path.unlink()


# ── episodes ────────────────────────────────────────────────────────────────

def list_episodes(pid: str) -> list:
    p = get_project(pid)
    if not p:
        return []
    return [_episode_summary(e) for e in p.get("episodes", [])]


def get_episode(pid: str, eid: str) -> Optional[dict]:
    p = get_project(pid)
    if not p:
        return None
    for e in p.get("episodes", []):
        if e["id"] == eid:
            return e
    return None


def first_episode_id(pid: str) -> Optional[str]:
    eps = (get_project(pid) or {}).get("episodes", [])
    return eps[0]["id"] if eps else None


def add_episode(pid: str, name: str, source_type: str, raw_text: str,
                parsed: dict) -> Optional[dict]:
    p = get_project(pid)
    if not p:
        return None
    next_idx = max((e.get("idx", 0) for e in p["episodes"]), default=0) + 1
    ep = _new_episode(next_idx, name, source_type=source_type,
                      raw_text=raw_text, parsed=parsed)
    p["episodes"].append(ep)
    _write(p)
    return ep


def update_episode(pid: str, eid: str, patch: dict[str, Any]) -> Optional[dict]:
    p = get_project(pid)
    if not p:
        return None
    for e in p["episodes"]:
        if e["id"] == eid:
            e.update(patch)
            e["updated_at"] = time.time()
            _write(p)
            return e
    return None


def delete_episode(pid: str, eid: str) -> Optional[dict]:
    p = get_project(pid)
    if not p:
        return None
    p["episodes"] = [e for e in p["episodes"] if e["id"] != eid]
    # keep idx contiguous + ordered
    for i, e in enumerate(p["episodes"], start=1):
        e["idx"] = i
    return _write(p)


def reorder_episodes(pid: str, order: list[str]) -> Optional[dict]:
    p = get_project(pid)
    if not p:
        return None
    by_id = {e["id"]: e for e in p["episodes"]}
    new_list = [by_id[i] for i in order if i in by_id]
    # append any episodes the caller forgot, preserving them
    new_list += [e for e in p["episodes"] if e["id"] not in set(order)]
    for i, e in enumerate(new_list, start=1):
        e["idx"] = i
    p["episodes"] = new_list
    return _write(p)


def prev_episode(pid: str, eid: str) -> Optional[dict]:
    """The episode immediately before `eid` in order (for cross-episode handoff)."""
    eps = (get_project(pid) or {}).get("episodes", [])
    for i, e in enumerate(eps):
        if e["id"] == eid:
            return eps[i - 1] if i > 0 else None
    return None
