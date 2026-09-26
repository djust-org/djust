# ADR-037 D2: browser pass on the interactive DropdownMenu catalogue entry

Date: 2026-09-25. Commit `487f61bf1` (branch `feat/adr-037-d2-d3`).

**Setup.** This worktree's `python/` was served by uvicorn on
`http://localhost:8019`. The harness was a scratch project using the demo project's settings, with the
theming URLs mounted at `/theme/`, `DEBUG=True`, and
`djust.theming.gallery.live_views` added to `LIVEVIEW_ALLOWED_MODULES`. Clicks
were made by coordinate in Chrome. Recording: `catalogue_interactive_dropdown.gif`
(40 frames, kept out of the repository).

Page: `/theme/components/interactive_dropdown_menu/`.

| # | Check | Result |
|---|---|---|
| 1 | Open Project, choose Edit | Pass. "Selected: edit". |
| 2 | Open Beta's row menu, choose Details | Pass. "Row action: 87:details", and "Selected" is unchanged. |
| 3 | Open one menu while another is open | Both stay open: Alpha and Beta both report `aria-expanded="true"`, and their popovers overlap. This is the documented contract ("opening one menu never changes another menu", `docs/website/guides/interactive-components.md:103`). An open popover also covers the rows below it, so a click there picks the popover's item. |
| 4 | Delete is disabled | Pass. It is shown greyed and cannot be chosen. The server also refuses it (`catalogue-dropdown` scenario). |
| 5 | A refused choice is visible | Pass. `djust.handleEvent('select', {component_id: <beta>, value: 'nope'})` dispatched `djust:error` ("Unknown or disabled dropdown selection"). Under DEBUG the server-error overlay and a toast showed it. Row action was unchanged. |
| 6 | Sidebar lists and navigates to the entry | Pass. From `/theme/components/button/`, the sidebar link navigated to the page, and the menu worked after the navigation. |

**Harness finding (not a defect).** The first load used the demo project's
`LIVEVIEW_ALLOWED_MODULES` unchanged, and the WebSocket mount was refused ("View … is
not allowed"). The same refusal applies to every catalogue page in any project that
sets an allowlist without `djust.theming.gallery.live_views`. It is the
existing F22 mount allowlist working as designed.
