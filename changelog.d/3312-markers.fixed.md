- **`djust init` handles marker-declared and duplicate requirements, and reports extras in `-r` files (#3312).**
  A `pyproject.toml` dependency declared with an environment marker (`uvicorn>=0.30; python_version>'3.9'`) no longer
  gets `uv add 'uvicorn[standard]'`, which appended a second unconditional line instead of keeping the specifier:
  `init` reports an ATTENTION step and the extra to add by hand. An extra now counts as present only on an unmarked
  declaration, so a marked pair no longer adds up to "satisfied" (pyproject.toml and requirements.txt); a marked-only
  extra in requirements.txt is reported, and an unmarked line still gets the extra in place while marked lines are left
  alone. An extra missing only in a `-r` included file, which `init` does not edit, is reported (ATTENTION step plus a
  note naming the file) instead of skipped silently. 20 regression cases in
  `python/djust/tests/test_djust_init_markers_3312.py`.
