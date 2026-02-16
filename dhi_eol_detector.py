#!/usr/bin/env python3
"""
DHI EOL Detector

Extracts the base image from a Docker image, verifies whether it is a
Docker Hardened Image (DHI), and if so, retrieves its End of Life /
End of Support dates from the Docker Scout GraphQL API.
"""

import argparse
from datetime import date, datetime
import json
import os
import re
import subprocess
import sys

import requests

# ─── Constants ────────────────────────────────────────────────────────────────

DOCKER_AUTH_URL = "https://hub.docker.com/v2/auth/token"
GRAPHQL_URL = "https://api.scout.docker.com/v1/graphql"

# ANSI colour helpers
_BOLD = "\033[1m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_CYAN = "\033[96m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _ok(msg: str) -> str:
    return f"  {_GREEN}✔{_RESET} {msg}"


def _warn(msg: str) -> str:
    return f"  {_YELLOW}⚠{_RESET} {msg}"


def _fail(msg: str) -> str:
    return f"  {_RED}✖{_RESET} {msg}"


def _info(msg: str) -> str:
    return f"  {_CYAN}ℹ{_RESET} {msg}"


def _header(msg: str) -> str:
    return f"\n{_BOLD}{msg}{_RESET}"


# ─── Image reference parsing ─────────────────────────────────────────────────

def parse_image_reference(image_ref: str) -> tuple[str, str | None]:
    """
    Parse a Docker image reference into (repository, tag).

    Handles formats like:
      - nginx
      - nginx:1.25
      - library/nginx:1.25
      - docker.io/library/nginx:1.25
      - docker/nginx-unprivileged:latest
      - myregistry.com/org/image:tag

    Returns (repository, tag) where tag may be None.
    """
    ref = image_ref.strip()

    # Strip known registry prefixes to normalise
    for prefix in ("docker.io/library/", "docker.io/", "index.docker.io/library/", "index.docker.io/"):
        if ref.startswith(prefix):
            ref = ref[len(prefix):]
            break

    # Handle digest references  (repo@sha256:...)
    if "@" in ref:
        ref = ref.split("@")[0]

    # Split tag
    tag = None
    if ":" in ref:
        parts = ref.rsplit(":", 1)
        ref = parts[0]
        tag = parts[1]

    # Strip leading "library/" (official images)
    if ref.startswith("library/"):
        ref = ref[len("library/"):]

    return ref, tag


# ─── Docker inspection ───────────────────────────────────────────────────────

def get_image_labels(image: str) -> dict:
    """Retrieve all labels from a Docker image via docker inspect, pulling if needed."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{json .Config.Labels}}", image],
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout.strip()) or {}
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        pass

    # Image not available locally — try pulling it
    print(_info(f"Image '{image}' not found locally, pulling..."))
    try:
        subprocess.run(
            ["docker", "pull", image],
            check=True,
        )
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{json .Config.Labels}}", image],
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout.strip()) or {}
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}


def extract_dhi_info(labels: dict) -> tuple[str | None, str | None]:
    """
    Extract DHI repository and version from Docker Hardened Image labels.

    Returns (repository, version) or (None, None) if not a DHI.
    """
    dhi_url = labels.get("com.docker.dhi.url")
    dhi_version = labels.get("com.docker.dhi.version")

    if not dhi_url:
        return None, None

    # Extract repository name from the DHI URL
    # e.g. https://hub.docker.com/r/docker/nginx-unprivileged -> nginx-unprivileged
    #      or it might just be a repo name directly
    repo = dhi_url
    # Strip trailing slashes
    repo = repo.rstrip("/")
    # If it's a full URL, extract the path after /r/
    if "/r/" in repo:
        repo = repo.split("/r/")[-1]
    # If it starts with http(s)://, strip everything up to the last path component
    elif repo.startswith("http"):
        repo = repo.split("/")[-1]

    return repo, dhi_version


def extract_base_image(labels: dict) -> tuple[str | None, str | None]:
    """
    Extract the base image reference from OCI labels.

    Returns (base_image_ref, base_digest) or (None, None).
    """
    base_name = labels.get("org.opencontainers.image.base.name")
    base_digest = labels.get("org.opencontainers.image.base.digest")
    return base_name, base_digest


# ─── Docker Scout GraphQL API ────────────────────────────────────────────────

def get_jwt_token() -> str:
    """Exchange Docker PAT for JWT token."""
    username = os.getenv("DOCKER_USERNAME")
    pat = os.getenv("DOCKER_PAT")

    if not username or not pat:
        print(_fail("DOCKER_USERNAME and DOCKER_PAT environment variables must be set."))
        sys.exit(1)

    payload = {"identifier": username, "secret": pat}

    try:
        resp = requests.post(DOCKER_AUTH_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        token = data.get("token") or data.get("access_token")
        if not token:
            print(_fail("Authentication succeeded but no token was returned."))
            sys.exit(1)
        return token
    except requests.exceptions.RequestException as e:
        print(_fail(f"Authentication failed: {e}"))
        sys.exit(1)


def _graphql(token: str, query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL query against the Docker Scout API."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "dhi-eol-detector/1.0",
    }
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables

    resp = requests.post(GRAPHQL_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_dhi_catalog(token: str) -> set[str]:
    """Fetch the full DHI catalog and return a set of repository names."""
    query = """
    query dhiListRepositories {
      dhiListRepositories {
        items {
          name
          type
        }
      }
    }
    """
    data = _graphql(token, query)
    items = data.get("data", {}).get("dhiListRepositories", {}).get("items", [])
    return {item["name"] for item in items if item.get("name")}


def fetch_eol_info(token: str, repo_name: str) -> list[dict]:
    """Fetch tag definitions (including EOL) for a DHI repository."""
    query = """
    query($repo: String!) {
      dhiRepository(repoName: $repo) {
        ... on DhiImageRepositoryDetails {
          tagDefinitions {
            displayName
            tagNames
            endOfLife
          }
        }
      }
    }
    """
    data = _graphql(token, query, variables={"repo": repo_name})
    repo_data = data.get("data", {}).get("dhiRepository", {})
    return repo_data.get("tagDefinitions", [])


# ─── Matching logic ──────────────────────────────────────────────────────────

def find_matching_tag_definition(tag: str | None, tag_definitions: list[dict]) -> dict | None:
    """
    Find the tag definition that matches the given tag.

    Tag definitions contain a list of tagNames (e.g. ["2", "2.39", "2.39.0"]).
    We look for an exact match first, then a prefix match.
    """
    if not tag:
        # If no tag was specified, return the first definition (typically "latest")
        for td in tag_definitions:
            if "latest" in (td.get("tagNames") or []):
                return td
        return tag_definitions[0] if tag_definitions else None

    # Exact match
    for td in tag_definitions:
        if tag in (td.get("tagNames") or []):
            return td

    # Prefix match (e.g. tag "2" could match definition with tagName "2.39")
    for td in tag_definitions:
        for tn in (td.get("tagNames") or []):
            if tn.startswith(tag) or tag.startswith(tn):
                return td

    return None


# ─── Main flow ────────────────────────────────────────────────────────────────

def run(image: str) -> None:
    """Main detection flow."""

    print(_header(f"🔍 DHI EOL Detector — analysing '{image}'"))
    print()

    # ── Step 1: Inspect image labels ────────────────────────────────────────
    print(_header("Step 1: Inspecting image labels"))
    labels = get_image_labels(image)

    if not labels:
        print(_fail(f"Could not inspect image '{image}'. Is it pulled locally?"))
        print()
        return

    # Check for DHI-specific labels
    dhi_repo, dhi_version = extract_dhi_info(labels)

    if not dhi_repo:
        print(_fail("This image is NOT based on a Docker Hardened Image."))
        print(_info("No com.docker.dhi.url label found."))
        print()
        return

    print(_ok(f"This image is based on a Docker Hardened Image! ✅"))
    print(_info(f"com.docker.dhi.url: {_BOLD}{labels.get('com.docker.dhi.url')}{_RESET}"))
    print(_info(f"com.docker.dhi.version: {_BOLD}{labels.get('com.docker.dhi.version', 'N/A')}{_RESET}"))
    print(_info(f"DHI Repository: {_BOLD}{dhi_repo}{_RESET}"))
    if dhi_version:
        print(_info(f"DHI Version: {_BOLD}{dhi_version}{_RESET}"))

    # ── Step 2: Authenticate ────────────────────────────────────────────────
    print(_header("Step 2: Authenticating with Docker Hub"))
    token = get_jwt_token()
    print(_ok("Authentication successful."))

    # ── Step 3: Fetch EOL information ───────────────────────────────────────
    print(_header("Step 3: Fetching End of Life information"))
    tag_definitions = fetch_eol_info(token, dhi_repo)

    if not tag_definitions:
        print(_warn("No tag definitions found for this repository."))
        print()
        return

    # Use the DHI version label as the tag to match against
    tag_to_match = dhi_version
    matched_def = find_matching_tag_definition(tag_to_match, tag_definitions)

    if matched_def:
        display_name = matched_def.get("displayName", "N/A")
        eol = matched_def.get("endOfLife")
        tag_names = ", ".join(matched_def.get("tagNames", []))

        print(_ok(f"Matched tag definition: {_BOLD}{display_name}{_RESET}"))
        print(_info(f"Tags: {tag_names}"))

        if eol:
            print(f"  {_BOLD}End of Life:{_RESET} {_YELLOW}{eol}{_RESET}")
            try:
                eol_date = datetime.strptime(eol[:10], "%Y-%m-%d").date()
                today = date.today()
                delta = eol_date - today
                if delta.days < 0:
                    abs_days = abs(delta.days)
                    years, rem = divmod(abs_days, 365)
                    months, days = divmod(rem, 30)
                    parts = []
                    if years: parts.append(f"{years} year{'s' if years != 1 else ''}")
                    if months: parts.append(f"{months} month{'s' if months != 1 else ''}")
                    if days: parts.append(f"{days} day{'s' if days != 1 else ''}")
                    print(f"  {_RED}{_BOLD}⚠ PAST EOL by {', '.join(parts)}{_RESET}")
                else:
                    years, rem = divmod(delta.days, 365)
                    months, days = divmod(rem, 30)
                    parts = []
                    if years: parts.append(f"{years} year{'s' if years != 1 else ''}")
                    if months: parts.append(f"{months} month{'s' if months != 1 else ''}")
                    if days: parts.append(f"{days} day{'s' if days != 1 else ''}")
                    print(f"  {_GREEN}{', '.join(parts)} remaining{_RESET}")
            except (ValueError, TypeError):
                pass
        else:
            print(f"  {_BOLD}End of Life:{_RESET} {_GREEN}Not set (no planned EOL){_RESET}")
    else:
        print(_warn(f"No matching tag definition found for version '{tag_to_match}'."))
        print(_info("Available tag definitions:"))
        for td in tag_definitions:
            dn = td.get("displayName", "?")
            tags = ", ".join(td.get("tagNames", [])[:5])
            eol = td.get("endOfLife", "—")
            print(f"    • {_BOLD}{dn}{_RESET}  (tags: {tags})  EOL: {eol}")

    print()


def main():
    parser = argparse.ArgumentParser(
        prog="dhi-eol-detector",
        description="Detect if a Docker image uses a Docker Hardened Image base and check its EOL status.",
    )
    parser.add_argument(
        "image",
        help="Docker image to analyse (must be locally available, e.g. 'myapp:latest')",
    )
    args = parser.parse_args()
    run(args.image)


if __name__ == "__main__":
    main()
