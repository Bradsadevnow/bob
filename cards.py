#!/usr/bin/env python3
"""
Fetch MTG card data from Scryfall for a list of {"card_id","name"} entries.

Usage:
  python fetch_scryfall_cards.py --in cards.json --out cards_scryfall_out.json --missing missing_cards.json

Notes:
- Uses Scryfall "named" endpoint with fuzzy matching.
- Custom cards not on Scryfall will be recorded in missing_cards.json.
- Basic lands: your list includes "Basic Plains" etc; fuzzy lookup usually works.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests


SCRYFALL_NAMED_FUZZY = "https://api.scryfall.com/cards/named"
USER_AGENT = "mtg_core_scryfall_fetch/1.0 (contact: none)"


@dataclass
class FetchResult:
    ok: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    status_code: Optional[int] = None


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, payload: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=False)


def scryfall_fetch_named_fuzzy(
    session: requests.Session,
    name: str,
    *,
    timeout_s: float = 15.0,
    max_retries: int = 6,
    base_sleep_s: float = 0.25,
) -> FetchResult:
    """
    Fetch a card by name using Scryfall named fuzzy endpoint.
    Retries on transient errors and 429 rate limiting.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    params = {"fuzzy": name}

    sleep_s = base_sleep_s
    for attempt in range(1, max_retries + 1):
        try:
            r = session.get(SCRYFALL_NAMED_FUZZY, headers=headers, params=params, timeout=timeout_s)
        except requests.RequestException as e:
            if attempt == max_retries:
                return FetchResult(ok=False, error=f"request_exception: {e}")
            time.sleep(sleep_s)
            sleep_s *= 2
            continue

        status = r.status_code

        # Rate limit: respect Retry-After if present
        if status == 429:
            retry_after = r.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = sleep_s
            else:
                wait = sleep_s
            if attempt == max_retries:
                return FetchResult(ok=False, status_code=status, error="rate_limited_429")
            time.sleep(wait)
            sleep_s = max(sleep_s * 2, wait)
            continue

        # Transient server errors
        if 500 <= status <= 599:
            if attempt == max_retries:
                return FetchResult(ok=False, status_code=status, error=f"server_error_{status}")
            time.sleep(sleep_s)
            sleep_s *= 2
            continue

        # Not found / bad request
        if status != 200:
            try:
                err = r.json()
                err_msg = err.get("details") or err.get("error") or str(err)
            except Exception:
                err_msg = r.text[:200]
            return FetchResult(ok=False, status_code=status, error=f"http_{status}: {err_msg}")

        try:
            data = r.json()
        except Exception as e:
            return FetchResult(ok=False, status_code=status, error=f"bad_json: {e}")

        return FetchResult(ok=True, status_code=status, data=data)

    return FetchResult(ok=False, error="unexpected_retry_fallthrough")


def normalize_scryfall_card(raw: Dict[str, Any], *, card_id: str) -> Dict[str, Any]:
    """
    Normalize a Scryfall card object into the compact fields you asked for:
    rules text + mv/cmc + a few useful extras.
    Handles double-faced cards by concatenating face texts.
    """
    def concat_faces(field: str) -> Optional[str]:
        faces = raw.get("card_faces")
        if not faces:
            return raw.get(field)
        parts = []
        for face in faces:
            v = face.get(field)
            if v:
                parts.append(v)
        if not parts:
            return None
        return "\n//\n".join(parts)

    oracle_text = concat_faces("oracle_text")
    mana_cost = concat_faces("mana_cost")
    type_line = concat_faces("type_line")

    out: Dict[str, Any] = {
        "card_id": card_id,
        "name": raw.get("name"),
        "scryfall_id": raw.get("id"),
        "set": raw.get("set"),
        "collector_number": raw.get("collector_number"),
        "lang": raw.get("lang"),
        "released_at": raw.get("released_at"),
        "uri": raw.get("uri"),
        "scryfall_uri": raw.get("scryfall_uri"),
        "type_line": type_line,
        "mana_cost": mana_cost,
        "cmc": raw.get("cmc"),
        "oracle_text": oracle_text,
        "colors": raw.get("colors"),
        "color_identity": raw.get("color_identity"),
        "rarity": raw.get("rarity"),
    }

    # Creature stats / loyalty (single-faced: top-level, multi-faced: per face)
    if raw.get("card_faces"):
        faces_out = []
        for face in raw["card_faces"]:
            faces_out.append(
                {
                    "name": face.get("name"),
                    "type_line": face.get("type_line"),
                    "mana_cost": face.get("mana_cost"),
                    "oracle_text": face.get("oracle_text"),
                    "power": face.get("power"),
                    "toughness": face.get("toughness"),
                    "loyalty": face.get("loyalty"),
                }
            )
        out["card_faces"] = faces_out
    else:
        out["power"] = raw.get("power")
        out["toughness"] = raw.get("toughness")
        out["loyalty"] = raw.get("loyalty")

    # Useful for your art cache if you want it later
    image_uris = raw.get("image_uris")
    if image_uris:
        out["image_uris"] = {
            "small": image_uris.get("small"),
            "normal": image_uris.get("normal"),
            "large": image_uris.get("large")
        }

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch card data from Scryfall for a list of cards.json entries.")
    parser.add_argument("--in", dest="in_path", required=True, help="Path to cards.json (list of {card_id,name}).")
    parser.add_argument("--out", dest="out_path", default="cards_scryfall_out.json", help="Output JSON path.")
    parser.add_argument("--missing", dest="missing_path", default="missing_cards.json", help="Missing cards output path.")
    parser.add_argument("--sleep", dest="sleep_s", type=float, default=0.12, help="Base sleep between requests (seconds).")
    args = parser.parse_args()

    cards_in = load_json(args.in_path)
    if not isinstance(cards_in, list):
        raise SystemExit("Input JSON must be a list of objects: [{card_id,name}, ...]")

    session = requests.Session()

    out_cards: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []

    # Simple in-run cache by name to avoid duplicate lookups (if any)
    cache: Dict[str, FetchResult] = {}

    for idx, entry in enumerate(cards_in, start=1):
        card_id = entry.get("card_id")
        name = entry.get("name")
        if not card_id or not name:
            missing.append({"entry": entry, "error": "missing_card_id_or_name"})
            continue

        key = name.strip().lower()
        if key in cache:
            res = cache[key]
        else:
            res = scryfall_fetch_named_fuzzy(session, name)
            cache[key] = res

        if not res.ok or not res.data:
            missing.append(
                {
                    "card_id": card_id,
                    "name": name,
                    "error": res.error or "unknown_error",
                    "status_code": res.status_code
                }
            )
            continue

        out_cards.append(normalize_scryfall_card(res.data, card_id=card_id))

        # gentle pacing
        time.sleep(args.sleep_s)

        if idx % 25 == 0:
            print(f"[{idx}/{len(cards_in)}] fetched...")

    save_json(args.out_path, {"schema_version": 1, "cards": out_cards})
    save_json(args.missing_path, {"missing": missing})

    print(f"Done. Wrote {len(out_cards)} cards to {args.out_path}")
    if missing:
        print(f"Missing/unresolved: {len(missing)} (see {args.missing_path})")


if __name__ == "__main__":
    main()
