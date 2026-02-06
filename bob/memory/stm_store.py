from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bob.config import BobConfig


def _now_ts() -> float:
    return time.time()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class HashingEmbeddingFunction:
    """
    Lightweight, dependency-free embedding for STM (hashing trick).
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = int(dim) if dim > 0 else 256

    def __call__(self, texts: List[str]) -> List[List[float]]:
        return [self._embed(t) for t in texts]

    def _embed(self, text: str) -> List[float]:
        tokens = re.findall(r"[a-z0-9_]+", (text or "").lower())
        vec = [0.0] * self.dim
        for tok in tokens:
            h = hashlib.md5(tok.encode("utf-8")).hexdigest()
            idx = int(h, 16) % self.dim
            vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


@dataclass
class STMHit:
    id: str
    text: str
    distance: float
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "distance": float(self.distance),
            "metadata": dict(self.metadata or {}),
        }


class STMStore:
    backend = "chroma"

    def __init__(
        self,
        *,
        path: str,
        collection: str,
        ttl_hours: int = 72,
        inject_refresh_hours: int = 24,
        top_k: int = 6,
        embedding_dim: int = 256,
        max_entries: int = 200,
        max_entry_chars: int = 3072,
    ) -> None:
        from chromadb import PersistentClient

        self.ttl_seconds = max(1, int(ttl_hours)) * 3600
        self.inject_refresh_seconds = max(1, int(inject_refresh_hours)) * 3600
        self.top_k = max(1, int(top_k))
        self.max_entries = max(1, int(max_entries))
        self.max_entry_chars = max(256, int(max_entry_chars))

        self.client = PersistentClient(path=path)
        self.collection = self.client.get_or_create_collection(
            name=collection,
            embedding_function=HashingEmbeddingFunction(dim=embedding_dim),
        )

    def add_turn(self, *, text: str, session_id: str, turn_number: int, error_tainted: bool = False) -> str:
        now_ts = _now_ts()
        expires_at = now_ts + self.ttl_seconds
        doc_id = f"stm_{uuid.uuid4()}"
        if text is None:
            text = ""
        text = str(text)
        if len(text) > self.max_entry_chars:
            text = text[: max(0, self.max_entry_chars - 3)] + "..."
        created_at = _now_utc()
        meta = {
            "created_at": created_at,
            "created_at_utc": created_at,
            "created_at_ts": now_ts,
            "expires_at_utc": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(timespec="seconds"),
            "expires_at_ts": expires_at,
            "last_injected_at_utc": None,
            "last_injected_at_ts": None,
            "last_accessed": None,
            "last_accessed_utc": None,
            "last_accessed_ts": None,
            "access_count": 0,
            "sessions_seen": 1,
            "last_session_id": str(session_id),
            "error_tainted": bool(error_tainted),
            "promotion_status": "none",
            "session_id": session_id,
            "turn_number": int(turn_number),
        }
        self.collection.add(documents=[text], metadatas=[meta], ids=[doc_id])
        self._enforce_limits()
        return doc_id

    def query(self, *, query_text: str, session_id: str, k: Optional[int] = None) -> List[Dict[str, Any]]:
        q = (query_text or "").strip()
        if not q:
            return []

        now_ts = _now_ts()
        self.prune_expired(now_ts)
        n = max(1, int(k or self.top_k))

        res = self.collection.query(
            query_texts=[q],
            n_results=n,
            where={"expires_at_ts": {"$gt": now_ts}},
        )

        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]

        hits: List[STMHit] = []
        update_ids: List[str] = []
        update_metas: List[Dict[str, Any]] = []

        for i, doc_id in enumerate(ids):
            if doc_id is None:
                continue
            doc = docs[i] if i < len(docs) else ""
            meta = metas[i] if i < len(metas) else {}
            dist = dists[i] if i < len(dists) else 0.0

            last_inj = meta.get("last_injected_at_ts")
            if last_inj is not None:
                try:
                    last_inj = float(last_inj)
                except Exception:
                    last_inj = None

            # enforce 24h injection refresh
            if last_inj is not None and (now_ts - last_inj) < self.inject_refresh_seconds:
                continue

            hits.append(STMHit(id=str(doc_id), text=str(doc), distance=float(dist), metadata=dict(meta)))

            meta_upd = dict(meta)
            meta_upd["last_injected_at_ts"] = now_ts
            meta_upd["last_injected_at_utc"] = _now_utc()
            meta_upd["last_accessed_ts"] = now_ts
            meta_upd["last_accessed_utc"] = _now_utc()
            meta_upd["last_accessed"] = meta_upd["last_accessed_utc"]
            try:
                meta_upd["access_count"] = int(meta_upd.get("access_count") or 0) + 1
            except Exception:
                meta_upd["access_count"] = 1
            last_session_id = str(meta_upd.get("last_session_id") or "")
            if session_id and last_session_id != str(session_id):
                try:
                    meta_upd["sessions_seen"] = int(meta_upd.get("sessions_seen") or 0) + 1
                except Exception:
                    meta_upd["sessions_seen"] = 1
                meta_upd["last_session_id"] = str(session_id)
            update_ids.append(str(doc_id))
            update_metas.append(meta_upd)

        if update_ids:
            self.collection.update(ids=update_ids, metadatas=update_metas)

        return [h.to_dict() for h in hits]

    def prune_expired(self, now_ts: Optional[float] = None) -> None:
        now_ts = now_ts or _now_ts()
        res = self.collection.get(where={"expires_at_ts": {"$lte": now_ts}})
        ids = res.get("ids") or []
        if ids:
            self.collection.delete(ids=ids)

    def _enforce_limits(self) -> None:
        res = self.collection.get()
        ids = res.get("ids") or []
        metas = res.get("metadatas") or []
        if not ids or len(ids) <= self.max_entries:
            return

        rows: list[tuple[str, float]] = []
        for i, doc_id in enumerate(ids):
            meta = metas[i] if i < len(metas) else {}
            ts = meta.get("created_at_ts")
            try:
                ts_val = float(ts)
            except Exception:
                ts_val = 0.0
            rows.append((str(doc_id), ts_val))

        rows.sort(key=lambda r: r[1])
        excess = len(rows) - self.max_entries
        if excess > 0:
            delete_ids = [doc_id for doc_id, _ in rows[:excess]]
            if delete_ids:
                self.collection.delete(ids=delete_ids)

    def promotion_candidates(
        self, *, access_count_min: int = 3, sessions_seen_min: int = 2, limit: int = 2
    ) -> List[Dict[str, Any]]:
        now_ts = _now_ts()
        self.prune_expired(now_ts)
        res = self.collection.get(where={"expires_at_ts": {"$gt": now_ts}})
        ids = res.get("ids") or []
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []

        out: List[Dict[str, Any]] = []
        update_ids: List[str] = []
        update_metas: List[Dict[str, Any]] = []

        for i, doc_id in enumerate(ids):
            if doc_id is None:
                continue
            meta = metas[i] if i < len(metas) else {}
            if meta.get("error_tainted"):
                continue
            status = str(meta.get("promotion_status") or "none")
            if status != "none":
                continue
            try:
                access_count = int(meta.get("access_count") or 0)
            except Exception:
                access_count = 0
            try:
                sessions_seen = int(meta.get("sessions_seen") or 0)
            except Exception:
                sessions_seen = 0
            if access_count < max(1, int(access_count_min)):
                continue
            if sessions_seen < max(1, int(sessions_seen_min)):
                continue

            doc = docs[i] if i < len(docs) else ""
            text = str(doc or "").strip()
            if not text:
                continue

            out.append({"id": str(doc_id), "text": text, "metadata": dict(meta)})

            meta_upd = dict(meta)
            meta_upd["promotion_status"] = "attempted"
            meta_upd["promotion_attempted_at_utc"] = _now_utc()
            update_ids.append(str(doc_id))
            update_metas.append(meta_upd)

            if len(out) >= max(1, int(limit)):
                break

        if update_ids:
            self.collection.update(ids=update_ids, metadatas=update_metas)

        return out

    def mark_promotion_result(
        self,
        *,
        stm_id: str,
        approved: bool,
        reviewer: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        if not stm_id:
            return
        try:
            res = self.collection.get(ids=[stm_id])
        except Exception:
            return
        ids = res.get("ids") or []
        metas = res.get("metadatas") or []
        if not ids or not metas:
            return

        meta = metas[0] or {}
        meta_upd = dict(meta)
        meta_upd["promotion_status"] = "approved" if approved else "rejected"
        meta_upd["promotion_decided_at_utc"] = _now_utc()
        if reviewer:
            meta_upd["promotion_reviewer"] = str(reviewer)
        if note:
            meta_upd["promotion_note"] = str(note)

        try:
            self.collection.update(ids=[stm_id], metadatas=[meta_upd])
        except Exception:
            return

    def dump(self, *, limit: int = 50, include_expired: bool = False) -> List[Dict[str, Any]]:
        now_ts = _now_ts()
        where = {}
        if not include_expired:
            where = {"expires_at_ts": {"$gt": now_ts}}

        res = self.collection.get(where=where, limit=max(1, int(limit)))
        ids = res.get("ids") or []
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []

        rows: List[Dict[str, Any]] = []
        for i, doc_id in enumerate(ids):
            doc = docs[i] if i < len(docs) else ""
            meta = metas[i] if i < len(metas) else {}
            rows.append({"id": str(doc_id), "text": str(doc), "metadata": dict(meta or {})})

        # sort by created_at_ts desc when available
        def _key(r: Dict[str, Any]) -> float:
            m = r.get("metadata") or {}
            ts = m.get("created_at_ts")
            try:
                return float(ts)
            except Exception:
                return 0.0

        rows.sort(key=_key, reverse=True)
        return rows


class STMJsonlStore:
    backend = "jsonl"

    def __init__(
        self,
        *,
        path: str,
        ttl_hours: int = 72,
        inject_refresh_hours: int = 24,
        top_k: int = 6,
        max_entries: int = 200,
        max_entry_chars: int = 3072,
    ) -> None:
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.ttl_seconds = max(1, int(ttl_hours)) * 3600
        self.inject_refresh_seconds = max(1, int(inject_refresh_hours)) * 3600
        self.top_k = max(1, int(top_k))
        self.max_entries = max(1, int(max_entries))
        self.max_entry_chars = max(256, int(max_entry_chars))

    def add_turn(self, *, text: str, session_id: str, turn_number: int, error_tainted: bool = False) -> str:
        now_ts = _now_ts()
        expires_at = now_ts + self.ttl_seconds
        doc_id = f"stm_{uuid.uuid4()}"
        if text is None:
            text = ""
        text = str(text)
        if len(text) > self.max_entry_chars:
            text = text[: max(0, self.max_entry_chars - 3)] + "..."
        created_at = _now_utc()
        meta = {
            "created_at": created_at,
            "created_at_utc": created_at,
            "created_at_ts": now_ts,
            "expires_at_utc": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(timespec="seconds"),
            "expires_at_ts": expires_at,
            "last_injected_at_utc": None,
            "last_injected_at_ts": None,
            "last_accessed": None,
            "last_accessed_utc": None,
            "last_accessed_ts": None,
            "access_count": 0,
            "sessions_seen": 1,
            "last_session_id": str(session_id),
            "error_tainted": bool(error_tainted),
            "promotion_status": "none",
            "session_id": session_id,
            "turn_number": int(turn_number),
        }
        rows = self._load_rows()
        rows.append({"id": doc_id, "text": text, "metadata": meta})
        rows = self._prune_rows(rows, now_ts=now_ts)
        rows = self._enforce_limits(rows)
        self._write_rows(rows)
        return doc_id

    def query(self, *, query_text: str, session_id: str, k: Optional[int] = None) -> List[Dict[str, Any]]:
        q = (query_text or "").strip()
        if not q:
            return []
        now_ts = _now_ts()
        rows = self._load_rows()
        rows = self._prune_rows(rows, now_ts=now_ts)

        q_tokens = self._tokenize(q)
        scored: list[tuple[float, Dict[str, Any]]] = []

        for row in rows:
            meta = row.get("metadata") or {}
            last_inj = meta.get("last_injected_at_ts")
            if last_inj is not None:
                try:
                    last_inj = float(last_inj)
                except Exception:
                    last_inj = None
            if last_inj is not None and (now_ts - last_inj) < self.inject_refresh_seconds:
                continue

            text = str(row.get("text") or "")
            score = self._similarity(q_tokens, self._tokenize(text))
            scored.append((score, row))

        scored.sort(key=lambda t: t[0], reverse=True)
        n = max(1, int(k or self.top_k))
        hits: List[STMHit] = []
        updated = False

        for score, row in scored[:n]:
            meta = row.get("metadata") or {}
            row_id = str(row.get("id") or "")
            text = str(row.get("text") or "")
            dist = 1.0 - float(score)
            hits.append(STMHit(id=row_id, text=text, distance=dist, metadata=dict(meta)).to_dict())

            meta_upd = dict(meta)
            meta_upd["last_injected_at_ts"] = now_ts
            meta_upd["last_injected_at_utc"] = _now_utc()
            meta_upd["last_accessed_ts"] = now_ts
            meta_upd["last_accessed_utc"] = _now_utc()
            meta_upd["last_accessed"] = meta_upd["last_accessed_utc"]
            try:
                meta_upd["access_count"] = int(meta_upd.get("access_count") or 0) + 1
            except Exception:
                meta_upd["access_count"] = 1
            last_session_id = str(meta_upd.get("last_session_id") or "")
            if session_id and last_session_id != str(session_id):
                try:
                    meta_upd["sessions_seen"] = int(meta_upd.get("sessions_seen") or 0) + 1
                except Exception:
                    meta_upd["sessions_seen"] = 1
                meta_upd["last_session_id"] = str(session_id)
            row["metadata"] = meta_upd
            updated = True

        if updated:
            self._write_rows(rows)
        return hits

    def prune_expired(self, now_ts: Optional[float] = None) -> None:
        now_ts = now_ts or _now_ts()
        rows = self._load_rows()
        rows = self._prune_rows(rows, now_ts=now_ts)
        self._write_rows(rows)

    def promotion_candidates(
        self, *, access_count_min: int = 3, sessions_seen_min: int = 2, limit: int = 2
    ) -> List[Dict[str, Any]]:
        now_ts = _now_ts()
        rows = self._load_rows()
        rows = self._prune_rows(rows, now_ts=now_ts)

        out: List[Dict[str, Any]] = []
        updated = False

        for row in rows:
            meta = row.get("metadata") or {}
            if meta.get("error_tainted"):
                continue
            status = str(meta.get("promotion_status") or "none")
            if status != "none":
                continue
            try:
                access_count = int(meta.get("access_count") or 0)
            except Exception:
                access_count = 0
            try:
                sessions_seen = int(meta.get("sessions_seen") or 0)
            except Exception:
                sessions_seen = 0
            if access_count < max(1, int(access_count_min)):
                continue
            if sessions_seen < max(1, int(sessions_seen_min)):
                continue

            text = str(row.get("text") or "").strip()
            if not text:
                continue
            out.append({"id": str(row.get("id") or ""), "text": text, "metadata": dict(meta)})

            meta_upd = dict(meta)
            meta_upd["promotion_status"] = "attempted"
            meta_upd["promotion_attempted_at_utc"] = _now_utc()
            row["metadata"] = meta_upd
            updated = True

            if len(out) >= max(1, int(limit)):
                break

        if updated:
            self._write_rows(rows)
        return out

    def mark_promotion_result(
        self,
        *,
        stm_id: str,
        approved: bool,
        reviewer: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        if not stm_id:
            return
        rows = self._load_rows()
        updated = False
        for row in rows:
            if str(row.get("id") or "") != str(stm_id):
                continue
            meta = row.get("metadata") or {}
            meta_upd = dict(meta)
            meta_upd["promotion_status"] = "approved" if approved else "rejected"
            meta_upd["promotion_decided_at_utc"] = _now_utc()
            if reviewer:
                meta_upd["promotion_reviewer"] = str(reviewer)
            if note:
                meta_upd["promotion_note"] = str(note)
            row["metadata"] = meta_upd
            updated = True
            break
        if updated:
            self._write_rows(rows)

    def dump(self, *, limit: int = 50, include_expired: bool = False) -> List[Dict[str, Any]]:
        now_ts = _now_ts()
        rows = self._load_rows()
        if not include_expired:
            rows = self._prune_rows(rows, now_ts=now_ts)

        def _key(r: Dict[str, Any]) -> float:
            m = r.get("metadata") or {}
            ts = m.get("created_at_ts")
            try:
                return float(ts)
            except Exception:
                return 0.0

        rows.sort(key=_key, reverse=True)
        return rows[: max(1, int(limit))]

    def _load_rows(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        rows: List[Dict[str, Any]] = []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    rows.append(obj)
        except Exception:
            return []
        return rows

    def _write_rows(self, rows: List[Dict[str, Any]]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _prune_rows(self, rows: List[Dict[str, Any]], *, now_ts: float) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for row in rows:
            meta = row.get("metadata") or {}
            expires = meta.get("expires_at_ts")
            try:
                expires_ts = float(expires)
            except Exception:
                expires_ts = None
            if expires_ts is not None and expires_ts <= now_ts:
                continue
            out.append(row)
        return out

    def _enforce_limits(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if len(rows) <= self.max_entries:
            return rows

        def _key(r: Dict[str, Any]) -> float:
            m = r.get("metadata") or {}
            ts = m.get("created_at_ts")
            try:
                return float(ts)
            except Exception:
                return 0.0

        rows.sort(key=_key)
        excess = len(rows) - self.max_entries
        if excess > 0:
            rows = rows[excess:]
        return rows

    def _tokenize(self, text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9_]+", (text or "").lower()))

    def _similarity(self, q_tokens: set[str], doc_tokens: set[str]) -> float:
        if not q_tokens and not doc_tokens:
            return 0.0
        if not q_tokens:
            return 0.0
        overlap = len(q_tokens & doc_tokens)
        return overlap / max(1, len(q_tokens))


def maybe_create_stm_store(cfg: BobConfig) -> Optional[STMStore]:
    if not getattr(cfg, "stm_enabled", True):
        return None
    try:
        return STMStore(
            path=cfg.stm_dir,
            collection=cfg.stm_collection,
            ttl_hours=cfg.stm_ttl_hours,
            inject_refresh_hours=cfg.stm_inject_refresh_hours,
            top_k=cfg.stm_top_k,
            embedding_dim=cfg.stm_embedding_dim,
            max_entries=getattr(cfg, "stm_max_entries", 200),
            max_entry_chars=getattr(cfg, "stm_max_entry_chars", 3072),
        )
    except Exception:
        fallback_path = os.path.join(cfg.stm_dir, "stm.jsonl")
        try:
            return STMJsonlStore(
                path=fallback_path,
                ttl_hours=cfg.stm_ttl_hours,
                inject_refresh_hours=cfg.stm_inject_refresh_hours,
                top_k=cfg.stm_top_k,
                max_entries=getattr(cfg, "stm_max_entries", 200),
                max_entry_chars=getattr(cfg, "stm_max_entry_chars", 3072),
            )
        except Exception:
            return None
