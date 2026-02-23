#!/usr/bin/env python3
"""
DHI EOL Detector

Inspects a Docker image, verifies whether it is a Docker Hardened Image
(DHI), and if so, reads its End of Life / End of Support date from the
image labels.
"""

import argparse
from datetime import date, datetime
import json
import subprocess
import sys

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


# ─── Main flow ────────────────────────────────────────────────────────────────

def _format_delta(delta_days: int) -> str:
    """Return a human-friendly breakdown of a number of days."""
    abs_days = abs(delta_days)
    years, rem = divmod(abs_days, 365)
    months, days = divmod(rem, 30)
    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if months:
        parts.append(f"{months} month{'s' if months != 1 else ''}")
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    return ", ".join(parts) if parts else "0 days"


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

    # ── Step 2: Check EOL from labels ───────────────────────────────────────
    print(_header("Step 2: Checking End of Life status"))
    eol = labels.get("com.docker.dhi.date.end-of-life")

    if eol:
        print(_info(f"com.docker.dhi.date.end-of-life: {_BOLD}{eol}{_RESET}"))
        try:
            eol_date = datetime.strptime(eol[:10], "%Y-%m-%d").date()
            today = date.today()
            delta = eol_date - today
            if delta.days < 0:
                print(f"  {_RED}{_BOLD}⚠ PAST EOL by {_format_delta(delta.days)}{_RESET}")
            else:
                print(f"  {_GREEN}{_format_delta(delta.days)} remaining{_RESET}")
        except (ValueError, TypeError):
            print(_warn("Could not parse the end-of-life date."))
    else:
        print(f"  {_BOLD}End of Life:{_RESET} {_GREEN}Not set (no planned EOL){_RESET}")

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
