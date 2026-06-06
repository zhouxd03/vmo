"""StoryState — cross-shot continuity memory (Phase 5).

Video models have no context memory between calls, so we keep an explicit state
layer that records, shot by shot, what the *next* shot must inherit:

  - scene / location
  - characters present + their blocking (站位/姿态)
  - props + their state (防穿帮: 形制/材质/位置)
  - lighting / mood
  - the tail frame of the previous shot (尾帧, for seamless continuation)
  - a free-form director note / handoff

``current`` is the live accumulator after the last committed shot; ``shots`` is
a per-shot snapshot archive (keyed by shot_no) that also stores the handoff
decision and AI-review verdict for that shot. Everything is persisted so a batch
can resume and the UI (分镜流水线) can visualise the whole chain.
"""

import time
import threading
from typing import Any, Optional

from .paths import PROJECTS_DIR
from .store import read_json, write_json

_locks_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}


def continuity_lock(pid: str) -> threading.RLock:
    with _locks_guard:
        lock = _locks.get(pid)
        if lock is None:
            lock = threading.RLock()
            _locks[pid] = lock
        return lock


def _state_path(pid: str):
    return PROJECTS_DIR / pid / "story_state.json"


def continuity_dir(pid: str):
    d = PROJECTS_DIR / pid / "continuity"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _empty_current() -> dict:
    return {
        "shot_no": None,
        "scene": "",
        "characters": [],   # [{name, position, pose}]
        "props": [],        # [{name, state}]
        "lighting": "",
        "tail_frame": None,     # filename under continuity/
        "director_note": "",
    }


def get_state(pid: str) -> dict:
    with continuity_lock(pid):
        st = read_json(_state_path(pid), None)
        if not st:
            st = {"pid": pid, "current": _empty_current(), "shots": {}, "updated_at": time.time()}
            write_json(_state_path(pid), st)
        st.setdefault("current", _empty_current())
        st.setdefault("shots", {})
        return st


def get_current(pid: str) -> dict:
    return get_state(pid)["current"]


def get_shot_state(pid: str, shot_no: str) -> Optional[dict]:
    return get_state(pid)["shots"].get(shot_no)


def _save(pid: str, st: dict) -> dict:
    st["updated_at"] = time.time()
    write_json(_state_path(pid), st)
    return st


def commit_shot(pid: str, shot_no: str, state: dict[str, Any]) -> dict:
    """Record the resulting state of *shot_no* and roll it forward as ``current``.

    *state* may contain any of: scene, characters, props, lighting, tail_frame,
    director_note. Missing keys inherit from the previous ``current`` so the
    chain never loses information (sliding-window handoff).
    """
    with continuity_lock(pid):
        st = get_state(pid)
        prev = st["current"]
        merged = _empty_current()
        merged.update(prev)
        merged.update({k: v for k, v in (state or {}).items() if v is not None})
        merged["shot_no"] = shot_no

        snapshot = dict(merged)
        prev_snap = st["shots"].get(shot_no, {})
        snapshot.setdefault("decision", prev_snap.get("decision"))
        snapshot.setdefault("review", prev_snap.get("review"))
        snapshot.setdefault("staging_image", prev_snap.get("staging_image"))
        snapshot.setdefault("director_board", prev_snap.get("director_board"))
        snapshot.setdefault("tail_frame", prev_snap.get("tail_frame"))
        snapshot.setdefault("decision_flushed", prev_snap.get("decision_flushed", False))
        snapshot.setdefault("staging_flushed", prev_snap.get("staging_flushed", False))
        snapshot.setdefault("director_flushed", prev_snap.get("director_flushed", False))
        for k in ("staging_image", "director_board", "tail_frame"):
            if snapshot.get(k):
                merged[k] = snapshot[k]
        st["shots"][shot_no] = snapshot
        st["current"] = merged
        return _save(pid, st)


def _update_shot_field(pid: str, shot_no: str, key: str, value: Any) -> dict:
    with continuity_lock(pid):
        st = get_state(pid)
        shot = st["shots"].get(shot_no) or {"shot_no": shot_no}
        shot[key] = value
        st["shots"][shot_no] = shot
        # mirror onto current when it is the active shot so the live accumulator
        # and per-shot archive stay in sync for the UI / resume logic.
        if st["current"].get("shot_no") == shot_no or st["current"].get("shot_no") is None:
            if key == "tail_frame":
                st["current"]["tail_frame"] = value
            elif key == "staging_image":
                st["current"]["staging_image"] = value
            elif key == "director_board":
                st["current"]["director_board"] = value
            elif key == "decision":
                st["current"]["decision"] = value
            elif key == "review":
                st["current"]["review"] = value
        return _save(pid, st)


def set_decision(pid: str, shot_no: str, decision: dict) -> dict:
    return _update_shot_field(pid, shot_no, "decision", decision)


def set_review(pid: str, shot_no: str, review: dict) -> dict:
    return _update_shot_field(pid, shot_no, "review", review)


def set_tail_frame(pid: str, shot_no: str, filename: str) -> dict:
    return _update_shot_field(pid, shot_no, "tail_frame", filename)


def set_staging(pid: str, shot_no: str, filename: str) -> dict:
    return _update_shot_field(pid, shot_no, "staging_image", filename)


def set_director_board(pid: str, shot_no: str, filename: str) -> dict:
    return _update_shot_field(pid, shot_no, "director_board", filename)


def clear_episode(pid: str, episode_no: int) -> dict:
    """Drop cached continuity decisions/images for one episode's shot numbers.

    Re-analysis/re-decomposition reuses shot numbers such as S01-C001. Keeping
    old continuity snapshots would make the new shots inherit stale staging /
    director / tail-frame decisions, so clear by episode prefix.
    """
    prefix = f"S{int(episode_no):02d}-"
    with continuity_lock(pid):
        st = get_state(pid)
        shots = st.get("shots") or {}
        st["shots"] = {
            no: snap for no, snap in shots.items()
            if not str(no).startswith(prefix)
        }
        if str((st.get("current") or {}).get("shot_no") or "").startswith(prefix):
            st["current"] = _empty_current()
        return _save(pid, st)


def reset(pid: str) -> dict:
    with continuity_lock(pid):
        st = {"pid": pid, "current": _empty_current(), "shots": {}, "updated_at": time.time()}
        write_json(_state_path(pid), st)
        return st
