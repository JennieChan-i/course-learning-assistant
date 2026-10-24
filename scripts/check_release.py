"""Fail publication if tracked files contain private paths, credentials or course excerpts."""

import csv
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DENIED = ("chunk/", "runtime/", "models/", ".cache/", ".venv/", "artifacts/private/")


def main():
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    paths = [p for p in result.stdout.decode("utf-8").split("\0") if p]
    problems = []
    private_snippets = []
    course = ROOT / "chunk/all_course_chunks_clean_v1.csv"
    if course.exists():
        with course.open(encoding="utf-8-sig", newline="") as f:
            private_snippets = [
                r["chunk_text"][:100] for r in csv.DictReader(f) if len(r["chunk_text"]) >= 100
            ]
    for name in paths:
        if name.startswith(DENIED) or (Path(name).name.startswith(".env") and name != ".env.example"):
            problems.append(f"Private path tracked: {name}")
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        if path.stat().st_size > 8 * 1024**2:
            problems.append(f"Unexpected large artifact: {name}")
        if path.suffix.lower() in (".png", ".jpg", ".webp", ".ico"):
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if re.search(
            r"(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|tvly-[A-Za-z0-9_-]{20,})",
            text,
        ):
            problems.append(f"Potential credential: {name}")
        if any(s in text for s in private_snippets):
            problems.append(f"Private course excerpt: {name}")
    if problems:
        raise SystemExit("\n".join(problems))
    print(
        f"PASS: {len(paths)} tracked files; no private paths, obvious credentials or matching course excerpts."
    )
    print(
        "Images still require visual review; this scanner cannot establish data rights or exhaustive secrecy."
    )


if __name__ == "__main__":
    main()
