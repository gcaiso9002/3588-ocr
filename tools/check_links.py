"""Check that every relative markdown link in the repo resolves."""
import os
import re
import sys

R = os.path.dirname(os.path.abspath(__file__))
pat = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
bad = 0
for base, _dirs, files in os.walk(R):
    if ".git" in base:
        continue
    for fn in files:
        if not fn.endswith(".md"):
            continue
        p = os.path.join(base, fn)
        rel = os.path.relpath(p, R)
        with open(p, encoding="utf-8") as f:
            text = f.read()
        for m in pat.finditer(text):
            link = m.group(1).strip()
            if link.startswith(("http://", "https://", "#", "mailto:")):
                continue
            path = link.split("#")[0]
            if not path:
                continue
            target = os.path.normpath(os.path.join(base, path))
            if not os.path.exists(target):
                print("BROKEN  %s -> %s" % (rel, link))
                bad += 1
print("checked, broken links:", bad)
sys.exit(1 if bad else 0)
