"""#2181 — strike ROADMAP rows whose issues are all closed.

Single-script transformation (#1312). Rules, deliberately conservative:

  * Only `| **Pn** | ... |` rows with no existing `~~` are touched.
  * A row is struck ONLY if EVERY issue reference in its TITLE cell resolves
    to a CLOSED issue. A row citing an open issue anywhere in the title is
    left alone, so a partly-open row can never be marked shipped.
  * Refs that are PR numbers (not issues) are ignored for the decision --
    they say nothing about whether the work landed.
  * The target cell becomes `Shipped (PR #N)` only when GitHub actually
    reports a closing PR for the primary issue. Otherwise it becomes a bare
    `Shipped` -- never a guessed PR number (#1197: hallucinated PR refs are
    a cataloged failure mode in this repo).
"""

import re
import sys
import pathlib

ROADMAP = pathlib.Path("ROADMAP.md")
DRY = "--apply" not in sys.argv

state, closer = {}, {}
for line in open("/tmp/closers.tsv"):
    if line.startswith("#"):
        continue
    num, st, pr = (line.rstrip("\n").split("\t") + ["", ""])[:3]
    state[num] = st
    if pr.strip():
        closer[num] = pr.strip()

ROW = re.compile(r"^\|\s*\*\*P\d\*\*")
# Split on unescaped pipes only. A naive split('|') treats the `\|` inside a
# cell (e.g. the filter expression `x\|safe`) as a column separator, which
# makes a 4-cell row look like 5 and puts the target write into the middle of
# the description -- silent data loss. Caught on ROADMAP.md:3926.
CELL = re.compile(r"(?<!\\)\|")
out, struck, skipped_open, skipped_unknown = [], [], [], []

for lineno, line in enumerate(open(ROADMAP), 1):
    if not ROW.match(line) or "~~" in line:
        out.append(line)
        continue
    cells = CELL.split(line.rstrip("\n").strip().strip("|"))
    if len(cells) != 4:
        out.append(line)
        continue

    title = cells[1]
    refs = re.findall(r"#(\d{3,5})", title)
    issue_refs = [r for r in refs if state.get(r) in ("CLOSED", "OPEN")]
    if not issue_refs:
        skipped_unknown.append((lineno, title.strip()[:60]))
        out.append(line)
        continue
    if any(state[r] == "OPEN" for r in issue_refs):
        skipped_open.append((lineno, [r for r in issue_refs if state[r] == "OPEN"]))
        out.append(line)
        continue

    primary = issue_refs[-1]
    orig_desc = cells[2]
    cells[1] = f" ~~{title.strip()}~~ ✅ "
    cells[3] = f" Shipped (PR #{closer[primary]}) " if primary in closer else " Shipped "
    new_line = "|" + "|".join(cells) + "|\n"

    # Post-condition: the rewrite may only touch the title and target cells.
    # Anything else means the split mis-identified a boundary.
    check = CELL.split(new_line.rstrip("\n").strip().strip("|"))
    assert len(check) == 4, f"L{lineno}: cell count changed {len(check)} != 4"
    assert check[2] == orig_desc, f"L{lineno}: DESCRIPTION MUTATED"
    assert check[0] == cells[0], f"L{lineno}: priority cell mutated"

    out.append(new_line)
    struck.append((lineno, primary, closer.get(primary, "-")))

print(f"struck            : {len(struck)}")
print(f"left (open issue) : {len(skipped_open)}  {[s[1] for s in skipped_open]}")
print(f"left (no issue ref): {len(skipped_unknown)}")
for ln, t in skipped_unknown[:6]:
    print(f"    L{ln}: {t}")
print(f"with real PR      : {sum(1 for s in struck if s[2] != '-')}"
      f" / bare 'Shipped': {sum(1 for s in struck if s[2] == '-')}")

if DRY:
    print("\nDRY RUN — pass --apply to write")
else:
    ROADMAP.write_text("".join(out))
    print("\nWROTE ROADMAP.md")
