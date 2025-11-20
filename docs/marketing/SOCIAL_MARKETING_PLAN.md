# Social Marketing Plan: djust Launch

**Objective:** Generate immediate buzz, drive traffic to `djust.dev`, and acquire 500+ GitHub stars in the first week.
**Target Audience:** Django developers, Python full-stack engineers, Rust enthusiasts, and "No-JS" advocates.

---

## 🚨 Phase 0: Critical Prerequisites ("What We Need")

Before we can launch, these items **MUST** be ready.

### 1. Technical Polish
*   [ ] **PyPI Release:** Ensure `pip install djust` works perfectly on a fresh env.
*   [ ] **Documentation:** `QUICKSTART.md` must be bulletproof. A user should be able to copy-paste and run in < 5 mins.
*   [ ] **Demo App:** A live URL for the demo (e.g., `demo.djust.dev`) is 10x more effective than code screenshots.
*   [ ] **GitHub Polish:**
    *   `README.md` needs the "N+1" visual at the very top.
    *   `CONTRIBUTING.md` for new contributors.
    *   Issue Templates (Bug Report, Feature Request).

### 2. Marketing Assets
*   [ ] **The "N+1 Killer" Graphic:** High-contrast, Red vs Green SQL comparison. (Crucial for Twitter/Reddit).
*   [ ] **The "Speed Run" Video:** A 30-60s screen recording:
    *   Start with empty file.
    *   Import `LiveView`.
    *   Add `@debounce`.
    *   Show working search bar.
    *   *Voiceover or captions explaining "No JS written".*
*   [ ] **Social Card (OG Image):** A sleek 1200x630 image for link previews.
*   [ ] **Benchmark Bar Chart:** Visualizing the 37x speedup vs Django templates.

### 3. Community Setup
*   [ ] **Discord Server:** Set up channels (`#general`, `#help`, `#showcase`, `#announcements`).
*   [ ] **Twitter Account:** @djust_framework (Bio: "The Django LiveView Framework. Powered by Rust. 🦀").
*   [ ] **GitHub Discussions:** Enable this feature on the repo.

---

## 1. Core Narrative & Hooks

We need to cut through the noise. Our messaging must be bold, technical, and slightly controversial.

*   **The "Killer Feature" Hook:** "Django's N+1 query problem is solved. Automatically. By a compiler."
*   **The "Performance" Hook:** "We replaced Django's template engine with Rust. It's 37x faster."
*   **The "No-JS" Hook:** "Build a Spotify-like UI in pure Python. No React. No API endpoints."
*   **The "Architecture" Hook:** "Client-side behavior. Server-side code. 0.8ms latency."

---

## 2. Channel Strategy

### 🐦 Twitter / X (@djust_framework)
*   **Strategy:** High-frequency visual content. "Build in public" style.
*   **Tactics:**
    *   Share the **N+1 SQL Comparison** image.
    *   Post 10-second video clips of UI interactions.
    *   Engage with Django/Rust influencers (e.g., @adamchainz, @willmcgugan).
    *   Use threads to explain the "How it works" (JIT compiler, VDOM diffing).

### 🤖 Reddit (r/django, r/python, r/rust)
*   **Strategy:** Deep technical value. No marketing fluff.
*   **Tactics:**
    *   **r/django:** "I built a Rust-powered LiveView for Django that solves N+1 queries automatically." (Focus on the pain point).
    *   **r/rust:** "Embedding a Rust VDOM engine inside Python for sub-millisecond HTML patching." (Focus on the FFI/performance).
    *   **r/python:** "Stop writing API endpoints. A new approach to full-stack Python."

### 🍊 Hacker News (Show HN)
*   **Strategy:** The Technical Deep Dive.
*   **Title:** "Show HN: djust – Phoenix LiveView for Django, powered by Rust"
*   **First Comment:** A detailed technical breakdown of *how* we achieved 0.8ms patches and the JIT architecture. HN loves implementation details.

---

## 3. Launch Timeline (2-Week Sprint)

### Week 1: The Tease (Building Hype)

| Day | Channel | Content Idea | Asset |
| :--- | :--- | :--- | :--- |
| **Mon** | Twitter | "Django templates are about to get a LOT faster. 🦀 #rustlang #django" | Screenshot of benchmark (450ms vs 12ms) |
| **Tue** | Twitter | "The #1 performance killer in Django is N+1 queries. What if your framework fixed them for you?" | **The N+1 Visual (Red/Green)** |
| **Wed** | Reddit | Teaser post in r/django: "Working on a new LiveView approach. Thoughts on this API?" | Code snippet of `@debounce` decorator |
| **Thu** | Twitter | "Client-side state, server-side logic. 0.8ms VDOM patching. Coming soon." | Video of instant UI updates |
| **Fri** | Twitter | "Monday. 🚀" | Logo animation / "Turbocharged D" |

### Week 2: The Launch (Maximum Noise)

| Day | Channel | Content Idea | Asset |
| :--- | :--- | :--- | :--- |
| **Mon** | **ALL** | **LAUNCH DAY!** "Introducing djust v0.1.0." | Link to `djust.dev`, Launch Video |
| **Mon** | HN | "Show HN: djust – Rust-powered LiveView for Django" | Technical deep-dive comment |
| **Tue** | Twitter | "How we solved N+1 queries with a JIT compiler." (Thread) | Architecture diagrams |
| **Wed** | Reddit | r/python deep dive: "Why we chose Rust for the VDOM engine." | Rust/Python FFI code snippets |
| **Thu** | Twitter | "Building a Chat App in 10 minutes with djust." | Speed-coding video |
| **Fri** | Twitter | "Week 1 Recap: 500 stars! Thank you community." | Star graph, roadmap teaser |

---

## 4. Launch Day: Hour-by-Hour Run of Show

**Date:** [TBD]
**Time:** 9:00 AM EST (Best for US/EU overlap)

*   **08:00 AM:** Final check of `djust.dev`, PyPI package, and GitHub repo visibility.
*   **09:00 AM:** 🚀 **Publish "Show HN" post.** (This is the primary traffic driver).
*   **09:05 AM:** Post the "Technical Breakdown" comment on the HN thread.
*   **09:15 AM:** **Tweet the Launch Thread.** Pin it immediately.
*   **09:30 AM:** Post to r/django and r/python. (Use different titles/angles to avoid spam filters).
*   **10:00 AM:** Email/DM key influencers with a polite "Just launched this, thought you might find the Rust VDOM interesting."
*   **10:00 AM - 4:00 PM:** **War Room.** Reply to every comment, tweet, and issue. Be helpful, humble, and technical.
*   **05:00 PM:** Post a "Day 1 Recap" tweet thanking the community.

---

## 5. Draft Copy Bank

### Twitter Thread (Launch)
> 1/ Introducing djust: The Django LiveView Framework. 🚀
>
> Build reactive, real-time apps in pure Python.
> No JavaScript. No API endpoints. No build step.
>
> Powered by a custom Rust VDOM engine for 0.8ms updates. 🦀
>
> pip install djust
>
> 🧵 A thread on how it works...

### Hacker News Comment (First Comment)
> OP here! 👋
>
> We built djust because we love Django but missed the interactivity of SPAs (and didn't want the complexity of React/Vue).
>
> Key technical details:
> 1. **Rust Core:** We replaced the template engine with a Rust implementation that's ~37x faster.
> 2. **JIT Optimization:** It analyzes your templates to automatically inject `select_related` calls, solving N+1 queries.
> 3. **Actor System:** Every user session is an isolated actor, allowing for massive concurrency without GIL contention.
>
> Happy to answer any questions about the architecture or the Rust/Python FFI!

### Reddit Post (r/django)
**Title:** I built a Rust-powered LiveView for Django that solves N+1 queries automatically
**Body:**
> Hey r/django,
>
> I've been working on a new framework called **djust**. It's a LiveView implementation for Django (similar to Phoenix LiveView), but with a twist: the core engine is written in Rust.
>
> **Why?**
> 1. **Performance:** VDOM diffing takes <1ms.
> 2. **N+1 Solver:** It analyzes your template variable usage (e.g. `{{ book.author.name }}`) and automatically optimizes the ORM query.
>
> It's fully open source. Would love your feedback on the API design!
> [Link to GitHub]

---

## 6. Success Metrics

*   **GitHub Stars:** 500+ in Week 1.
*   **Traffic:** 10,000+ unique visitors to `djust.dev`.
*   **Community:** 100+ joins on Discord.
*   **Engagement:** Front page of Hacker News (Top 10).
