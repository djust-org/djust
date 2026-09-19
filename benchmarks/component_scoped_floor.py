"""ADR-032 §Measured — floor for a component-scoped render on the catalogue accordion page: what a component-scoped
render would cost vs the full-page render it replaces. Sequential; run alone."""

import asyncio, contextlib, statistics, time, uuid
import django
from django.conf import settings

settings.configure(
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    INSTALLED_APPS=[
        "django.contrib.contenttypes",
        "django.contrib.auth",
        "django.contrib.sessions",
        "djust",
        "djust.theming",
    ],
    SECRET_KEY="p",
    USE_TZ=True,
    ROOT_URLCONF="djust.tests.urls_theming",
    LIVEVIEW_ALLOWED_MODULES=None,
    DEBUG=False,
    TEMPLATES=[
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [],
            "APP_DIRS": True,
            "OPTIONS": {"builtins": ["djust.templatetags.live_tags"]},
        }
    ],
)
django.setup()
from django.test import RequestFactory
from djust.runtime import ViewRuntime
from djust._rust import diff_html
from djust.theming.gallery.live_views import ComponentsDetailView
from djust.theming.gallery.component_registry import render_python_component_example


class T:
    def __init__(s):
        s._session_id = str(uuid.uuid4())
        s._client_ip = None
        s.sent = []

    session_id = property(lambda s: s._session_id)
    client_ip = property(lambda s: s._client_ip)

    async def send(s, d):
        s.sent.append(d)

    async def send_error(s, e, **k):
        s.sent.append({"type": "error", "error": e})

    async def close(s, code=1000):
        pass

    def next_client_version(s, h, v):
        return v

    def build_request(s):
        return None

    def on_view_mounted(s, v):
        pass

    @contextlib.asynccontextmanager
    async def event_context(s, v):
        yield


def ms(samples):
    return f"p50 {statistics.median(samples):6.1f}  min {min(samples):6.1f}  ms (n={len(samples)})"


async def main():
    v = ComponentsDetailView()
    v.request = RequestFactory().get("/theme/components/accordion/")
    v.mount(v.request, component_name="accordion")
    v.render_with_diff()
    tr = T()
    rt = ViewRuntime(tr)
    rt.view_instance = v
    # A. today: full-page render per toggle (alias event path)
    full = []
    for i, val in enumerate(["1", "2", "3", "1", "2", "3", "1", "2"]):
        t0 = time.perf_counter()
        await rt.dispatch_event(
            {"type": "event", "event": "accordion_toggle", "params": {"value": val}, "ref": i}
        )
        full.append((time.perf_counter() - t0) * 1000)
    print("A  full-page toggle (today)          ", ms(full))
    # B. component-only render: the accordion example against the two states
    ex = [
        dict(x)
        for x in __import__(
            "djust.theming.gallery.component_registry", fromlist=["x"]
        ).PYTHON_COMPONENT_EXAMPLES["accordion"]
    ]
    kw = ex[0]
    comp = []
    htmls = {}
    for val in ["1", "2", "3", "1", "2", "3", "1", "2"]:
        t0 = time.perf_counter()
        h = render_python_component_example("accordion", {**kw, "active": val})
        comp.append((time.perf_counter() - t0) * 1000)
        htmls[val] = h
    print("B  component render only            ", ms(comp), f"  ({len(htmls['1'])} bytes)")
    # C. subtree parse+diff (Rust) of the component's old vs new markup
    d = []
    pairs = [("1", "2"), ("2", "3"), ("3", "1")] * 3
    for a, b in pairs:
        t0 = time.perf_counter()
        patches = diff_html(htmls[a], htmls[b])
        d.append((time.perf_counter() - t0) * 1000)
    print("C  subtree parse+diff (Rust)        ", ms(d), f"  ({len(patches)} bytes of patches)")
    # D. the skip-gate snapshot pair, which any scoped path still pays
    from djust.websocket import _snapshot_assigns

    s = []
    for _ in range(9):
        t0 = time.perf_counter()
        _snapshot_assigns(v)
        _snapshot_assigns(v)
        s.append((time.perf_counter() - t0) * 1000)
    print("D  pre+post _snapshot_assigns       ", ms(s))
    print(
        f"\nscoped floor ≈ B + C + D = {statistics.median(comp) + statistics.median(d) + statistics.median(s):.1f} ms  vs  A = {statistics.median(full):.1f} ms"
    )


asyncio.run(main())
