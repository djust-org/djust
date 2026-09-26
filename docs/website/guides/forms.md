---
title: "Forms & Validation"
slug: forms
section: guides
order: 3
level: beginner
description: "Handle form submissions, validation, and user input in djust LiveViews"
---

# Forms

djust handles forms over WebSocket. No page reloads, no JavaScript, no API layer. You write a Python handler, add `dj-submit` to your `<form>`, and it works.

## The Simplest Form