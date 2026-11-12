---
id: audio.W001
---

<!-- doc: guide -->
### djust.audio.W001: Declared sound asset not found

**Severity**: Warning

**What causes it**: "Declared sound asset not found: <path>". The staticfiles finders can't locate a sound file declared in a bank.

**Fix**: Add the file to a staticfiles directory before `collectstatic`.
