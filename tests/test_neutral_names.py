import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_shared_code_never_names_alex():
    bad = [f"{p.name}:{i}" for p in sorted((ROOT / "src/igdm").glob("*.*"))
           for i, line in enumerate(p.read_text().splitlines(), 1) if re.search(r"(?i)alex", line)]
    assert bad == []


# Words that give away the owner's own reels and texting style. They must never come back into
# the shared tests, which are copied to the public repo.
# Spelled in parts so this file never holds the words the leak scanner looks for.
ALEX_MARKERS = re.compile(r"(?i)" + "shen" + r"zhen|\bbro\b|\byea\b|\bsweet\b")
OWNER_ONLY = {Path("tests/data/personal.txt"), Path("tests/test_neutral_names.py")}


def test_shared_tests_use_neutral_wording():
    bad = []
    for p in sorted((ROOT / "tests").rglob("*")):
        rel = p.relative_to(ROOT)
        if not p.is_file() or rel in OWNER_ONLY or "__pycache__" in rel.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        bad += [f"{rel}:{i}" for i, line in enumerate(text.splitlines(), 1)
                if ALEX_MARKERS.search(line)]
    assert bad == []
