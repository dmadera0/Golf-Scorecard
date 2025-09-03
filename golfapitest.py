#!/usr/bin/env python3
"""
Golf Course API CLI diagnostic tool
- Loads API key from .env (python-dotenv)
- Menu-driven CLI with:
  1) Search for Course (typeahead/autocomplete via prompt_toolkit if available)
  2) Health Check (validate key; optional email lookup on common endpoints)
  3) Search by State (input postal code like "NV" and list courses; select for details)

It auto-discovers API auth scheme and endpoint paths by probing common variants.

Install:
  pip3 install requests python-dotenv prompt_toolkit

Run:
  python3 golfapitest.py

Env vars:
  GOLF_API_KEY           (required) API key
  GOLF_API_BASE          default: https://api.golfcourseapi.com
  GOLF_API_SEARCH_PATH   optional exact search path to try first
  GOLF_API_DEBUG=1       to print attempted combos
"""
from __future__ import annotations

import os
import sys
import json
import re
from typing import Dict, Any, List, Optional

import requests
from dotenv import load_dotenv

# Try to import prompt_toolkit for nicer UX; fall back to input() if missing
try:
    from prompt_toolkit import prompt
    from prompt_toolkit.completion import Completer, Completion, FuzzyCompleter
    from prompt_toolkit.styles import Style
    PT_AVAILABLE = True
except Exception:
    PT_AVAILABLE = False

load_dotenv()

# ---------------- Config ----------------
GOLF_API_KEY = os.getenv("GOLF_API_KEY")
GOLF_API_BASE = os.getenv("GOLF_API_BASE", "https://api.golfcourseapi.com")
EXPLICIT_SEARCH_PATH = os.getenv("GOLF_API_SEARCH_PATH")
DEBUG = os.getenv("GOLF_API_DEBUG", "0") == "1"

if not GOLF_API_KEY:
    print("❌ GOLF_API_KEY not set in .env or environment.")
    print("   Add to .env: GOLF_API_KEY=YOUR_REAL_KEY")
    sys.exit(1)

# Candidate search paths & shapes
CANDIDATE_SEARCH_PATHS = [p for p in [
    EXPLICIT_SEARCH_PATH,
    "/v1/courses/search",
    "/api/v1/courses/search",
    "/v1/search/courses",
    "/api/v1/search/courses",
    "/v1/courses",
    "/api/v1/courses",
] if p]

# Get-by-id paths
CANDIDATE_GET_BY_ID_PATHS = [
    "/v1/courses/{id}",
    "/api/v1/courses/{id}",
]

# Health/Account paths (best-guess)
CANDIDATE_HEALTH_PATHS = [
    "/v1/health",
    "/api/v1/health",
    "/v1/account",
    "/api/v1/account",
    "/v1/accounts/lookup",
    "/api/v1/accounts/lookup",
]

AUTH_STYLES = ["key", "bearer", "x-api-key", "query"]
# Text search query keys we see in the wild
QUERY_KEYS = ["q", "query", "name", "course", "search"]
# State filter keys we see in the wild
STATE_KEYS = ["state", "state_code", "region", "admin_area", "province", "us_state", "countrySubdivision"]

STYLE = Style.from_dict({
    "prompt": "bold",
    "hint": "italic",
}) if PT_AVAILABLE else None

# ---------------- Helpers ----------------

def _api_url(path: str) -> str:
    base = GOLF_API_BASE.rstrip("/")
    path = path if path.startswith("/") else f"/{path}"
    return f"{base}{path}"


def _build_headers(style: str) -> Dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "golfapitest-cli/1.1"}
    if style == "key":
        headers["Authorization"] = f"Key {GOLF_API_KEY}"
    elif style == "bearer":
        headers["Authorization"] = f"Bearer {GOLF_API_KEY}"
    elif style == "x-api-key":
        headers["x-api-key"] = GOLF_API_KEY
    return headers


def _mask_key(key: str) -> str:
    return key if len(key) <= 8 else f"{key[:4]}…{key[-4:]}"


def _get(url: str, style: str, params: Dict[str, Any]) -> requests.Response:
    headers = _build_headers(style)
    p = dict(params)
    if style == "query":
        p["api_key"] = GOLF_API_KEY
    if DEBUG:
        print(f"[debug] GET {url} style={style} params={p}")
    return requests.get(url, headers=headers, params=p, timeout=12)

# ---------------- Core API discovery ----------------

def _normalize_courses(data: Any) -> List[Dict[str, Any]]:
    raw = data if isinstance(data, list) else (data.get("courses") or data.get("results") or data.get("data") or [])
    results: List[Dict[str, Any]] = []
    for c in raw:
        results.append({
            "id": c.get("id") or c.get("_id") or c.get("course_id"),
            "name": c.get("name") or c.get("course_name") or "(Unnamed Course)",
            "city": c.get("city") or c.get("town") or "",
            "state": c.get("state") or c.get("region") or "",
            "country": c.get("country") or "",
            "_raw": c,
        })
    return results


def search_courses(term: str, limit: int = 10) -> List[Dict[str, Any]]:
    # Try explicit path first then the matrix
    paths = []
    if EXPLICIT_SEARCH_PATH:
        paths.append(EXPLICIT_SEARCH_PATH)
    for p in CANDIDATE_SEARCH_PATHS:
        if p not in paths:
            paths.append(p)

    for path in paths:
        url = _api_url(path)
        for qkey in QUERY_KEYS:
            params = {qkey: term, "limit": limit}
            for style in AUTH_STYLES:
                try:
                    resp = _get(url, style, params)
                    if resp.status_code in (401, 403):
                        continue  # auth mismatch
                    if resp.status_code == 404:
                        break     # wrong path; next path
                    resp.raise_for_status()
                    data = resp.json() if resp.content else {}
                    results = _normalize_courses(data)
                    if results:
                        if DEBUG:
                            print(f"[debug] ✅ search ok path={path} qkey={qkey} auth={style}")
                        return results
                except requests.RequestException as e:
                    if DEBUG:
                        print(f"[debug] exception {e}")
                    continue
    return []


def search_courses_by_state(state_code: str, limit: int = 50) -> List[Dict[str, Any]]:
    # Uppercase and validate simple US postal code pattern (still allow non-US)
    state_code = state_code.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", state_code):
        # We'll still attempt with given string; many APIs accept full state names
        pass

    paths = []
    if EXPLICIT_SEARCH_PATH:
        paths.append(EXPLICIT_SEARCH_PATH)
    for p in CANDIDATE_SEARCH_PATHS:
        if p not in paths:
            paths.append(p)

    for path in paths:
        url = _api_url(path)
        for skey in STATE_KEYS:
            params = {skey: state_code, "limit": limit}
            for style in AUTH_STYLES:
                try:
                    resp = _get(url, style, params)
                    if resp.status_code in (401, 403):
                        continue
                    if resp.status_code == 404:
                        break
                    resp.raise_for_status()
                    data = resp.json() if resp.content else {}
                    results = _normalize_courses(data)
                    if results:
                        if DEBUG:
                            print(f"[debug] ✅ state search ok path={path} skey={skey} auth={style}")
                        return results
                except requests.RequestException:
                    continue
    return []


def get_course_by_id(course_id: str) -> Optional[Dict[str, Any]]:
    for tmpl in [p for p in CANDIDATE_GET_BY_ID_PATHS]:
        url = _api_url(tmpl.format(id=course_id))
        for style in AUTH_STYLES:
            try:
                resp = _get(url, style, {})
                if resp.status_code in (401, 403):
                    continue
                if resp.status_code == 404:
                    break
                resp.raise_for_status()
                return resp.json() if resp.content else {}
            except requests.RequestException:
                continue
    return None

# ---------------- Health check ----------------

def health_check(email: Optional[str] = None) -> Dict[str, Any]:
    report: Dict[str, Any] = {"base": GOLF_API_BASE, "key": _mask_key(GOLF_API_KEY), "key_valid": False}
    results = search_courses("test", limit=1)
    report["key_valid"] = bool(results)

    if email:
        report["email"] = email
        account_payload = None
        for path in [p for p in CANDIDATE_HEALTH_PATHS]:
            url = _api_url(path)
            for style in AUTH_STYLES:
                try:
                    resp = _get(url, style, {"email": email})
                    if resp.status_code == 404:
                        break
                    if resp.status_code in (401, 403):
                        continue
                    if resp.ok:
                        try:
                            account_payload = resp.json()
                        except Exception:
                            account_payload = {"_raw": resp.text}
                        break
                except requests.RequestException:
                    pass
            if account_payload:
                break
        report["account"] = account_payload
    return report

# ---------------- Autocomplete ----------------

class CourseCompleter(Completer):
    def __init__(self, limit: int = 8):
        self.limit = limit

    def get_completions(self, document, complete_event):
        text = document.text.strip()
        if not text or len(text) < 2:
            return
        try:
            results = search_courses(text, limit=self.limit)
        except Exception:
            results = []
        seen = set()
        for c in results:
            name = c.get("name") or ""
            city = c.get("city") or ""
            state = c.get("state") or ""
            label = name if not city and not state else f"{name} — {city}, {state}".strip()
            if label in seen:
                continue
            seen.add(label)
            yield Completion(name, start_position=-len(text), display=label)

# ---------------- CLI Views ----------------

def view_search():
    if PT_AVAILABLE:
        print("\nType a course name (autocomplete enabled). Press Enter to search.")
        completer = FuzzyCompleter(CourseCompleter())
        query = prompt([('class:prompt', 'Search: ')] , completer=completer, style=STYLE)
    else:
        query = input("Search: ")
    query = query.strip()
    if not query:
        print("❌ Please enter a course name.")
        return

    results = search_courses(query, limit=10)
    if not results:
        print("No courses found.")
        return

    print(f"\nResults for '{query}':")
    for idx, r in enumerate(results, 1):
        loc = ", ".join([b for b in [r.get('city',''), r.get('state',''), r.get('country','')] if b])
        print(f"{idx}. {r['name']}{(' — ' + loc) if loc else ''}  (id={r.get('id')})")

    try:
        choice = input("\nView details for which #? (Enter to skip): ").strip()
        if choice:
            i = int(choice)
            if 1 <= i <= len(results):
                cid = results[i-1].get("id")
                if cid:
                    detail = get_course_by_id(str(cid))
                    if detail:
                        print("\nCourse details:")
                        print(json.dumps(detail, indent=2)[:2000])
                    else:
                        print("(No details endpoint found for this tenant.)")
            else:
                print("(Out of range; skipping details.)")
    except Exception:
        pass


def view_search_by_state():
    print("\nEnter a state postal code (e.g., NV, CA, TX). Full state names may work too.")
    state = input("State: ").strip()
    if not state:
        print("❌ Please enter a state.")
        return

    results = search_courses_by_state(state, limit=50)
    if not results:
        print("No courses found for that state.")
        return

    print(f"\nCourses in {state.upper()}: (showing up to {min(len(results),50)})")
    for idx, r in enumerate(results[:50], 1):
        loc = ", ".join([b for b in [r.get('city',''), r.get('state',''), r.get('country','')] if b])
        print(f"{idx}. {r['name']}{(' — ' + loc) if loc else ''}  (id={r.get('id')})")

    try:
        choice = input("\nView details for which #? (Enter to skip): ").strip()
        if choice:
            i = int(choice)
            if 1 <= i <= min(len(results),50):
                cid = results[i-1].get("id")
                if cid:
                    detail = get_course_by_id(str(cid))
                    if detail:
                        print("\nCourse details:")
                        print(json.dumps(detail, indent=2)[:2000])
                    else:
                        print("(No details endpoint found for this tenant.)")
            else:
                print("(Out of range; skipping details.)")
    except Exception:
        pass


def view_health():
    email = input("Email for health check (Enter to skip): ").strip() or None
    report = health_check(email)
    print("\nHealth Report:")
    print(json.dumps(report, indent=2)[:4000])

# ---------------- Main Menu ----------------

def main():
    while True:
        print("\n=== Golf Course API CLI ===")
        print(f"API Base: {GOLF_API_BASE}")
        print(f"API Key:  {_mask_key(GOLF_API_KEY)}")
        print("1) Search for Course")
        print("2) Health Check")
        print("3) Search by State")
        print("4) Exit")
        choice = input("Select option: ").strip()
        if choice == "1":
            view_search()
        elif choice == "2":
            view_health()
        elif choice == "3":
            view_search_by_state()
        elif choice == "4":
            print("Goodbye!")
            break
        else:
            print("❌ Invalid choice")

if __name__ == "__main__":
    main()
