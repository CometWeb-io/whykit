# WhyKit Explorer

A read-only browser for a WhyKit vault. It shows the decisions, the evidence
register, the review queue and history, the wikilink graph and the lint
results, all derived from the Markdown files. Explorer has no database and no
server-side code. It renders one JSON index that the Python package generates.

Explorer is optional and is not part of the data contract. A vault is complete
without it. Explorer build and E2E/a11y jobs block the product release.

| View | What it shows |
|---|---|
| Home | Recent decisions, current sources of truth, integrity and open questions |
| Decisions | The append-only decision log, newest first, with supersession links |
| Timeline | How long each decision stayed in force: a bar from its date to the date of the decision that superseded it, filterable by status, owner and tag |
| Document | Any note, its registered evidence, links and backlinks; decision records also show their full supersession chain (for example D-009 → D-010 → D-011) |
| Evidence | The register, including retired rows, with a filter |
| Freshness | Each active source's access age against the `[evidence_access_age_days]` windows in `whykit.toml` (stale, due soon, fresh), and which notes in force still cite retired sources |
| Reviews | What is due within 30 days and the recorded review events |
| Graph | Notes grouped by workstream and connected by resolved wikilinks; click a note to select it and inspect its links, press Enter or double-click to open it. From the keyboard the graph is one Tab stop (a listbox with a roving tabindex): arrow keys move between notes, Home and End jump to the first and last, typing the start of a title moves to it, Space selects and Enter opens |
| Health | Lint errors and warnings, the status mix, and what the checks cannot tell you |

Press <kbd>Ctrl</kbd>/<kbd>⌘</kbd> + <kbd>K</kbd> anywhere to search titles,
ids, tags and bodies.

Every view keeps its state in the URL fragment: filters, the graph selection
and workstream, the evidence filter and an open search with its query. A
reload or a shared link (`#view=timeline&owner=Lena%20Kr%C3%BCger`,
`#view=graph&node=06-decisions%2Fd-010-direct`) shows the same screen. State
changes replace the current history entry, so Back leaves the page rather than
undoing keystrokes.

## Requirements

- Node.js 22.18 or newer and npm
- Python 3.11 or newer, used to build the index from the source checkout

Explorer ships with the source repository and is not included in the Python
package.

## Run it locally

From the repository root:

```bash
npm ci --prefix apps/explorer
uv run whykit serve examples/northline
```

`whykit serve` builds the index for the vault you name and starts the Vite dev
server on `http://127.0.0.1:5173`. It binds to loopback by default and refuses to
serve non-public documents on any other interface unless you pass
`--allow-sensitive-network`.

To work on Explorer itself without the CLI wrapper:

```bash
cd apps/explorer
npm run dev                                   # examples/northline
WHYKIT_VAULT_DIR=../../../my-ledger npm run dev   # any other vault
```

## The index

`npm run index` builds the index by calling the canonical Python
implementation, so front matter and tables are parsed in only one place:

```bash
uv run whykit explorer-index --root examples/northline --today 2026-09-17
```

The command prints a public index as JSON. It contains the surviving public documents' metadata and
body, the evidence IDs each note cites, the evidence, decision and review rows,
the freshness policy from `whykit.toml`, and the lint report. If the vault
has lint errors the command fails, and Explorer will not build from an invalid
vault.

`npm run index` then splits that index in three, because note bodies are most
of it (about 6.5 MB of 10 MB at 5,000 notes) and only the document view and
full-text search read them, and the lint findings (about 0.5 MB at 5,000
notes) are read only by Health:

| File | Loaded | Contents |
|---|---|---|
| `src/generated/vault.json` | bundled, before the first paint | everything except bodies and lint findings, plus each note's resolved links (computed at build time by the same resolver the browser uses), its count of review cues, and the lint error and warning counts |
| `src/generated/bodies.json` | fetched after the first paint, or at once for a link to a note or an open search | every note body, keyed by note id |
| `src/generated/findings.json` | fetched when Health opens | every lint finding |

Until the bodies arrive, search matches titles, ids, tags and summaries and
says so. `npm run check:index` checks that the files match the summary (note
for note, and finding for counted finding) and rejects any of them that
includes an absolute path from the build machine.

Public is the default for CLI export and npm builds. Notes with non-public or
unreadable labels, local unclassified attachments, or references to hidden
records are withheld as whole notes, including transitive references. Ledger
rows and findings follow their document visibility. A hub linking to a private
note can therefore disappear; classify a dedicated publication vault rather
than expecting redaction to preserve its meaning. The exporter cannot identify
arbitrary private prose copied into a note labelled public.

For a private local build, opt in with `WHYKIT_EXPLORER_PRIVATE=1 npm run build`
or `whykit explorer-index --private`. `whykit serve` opts in automatically and
keeps the existing network guard. Never publish such a build. The `--from`
script path imports unverified JSON and also requires the private opt-in.

Set `WHYKIT_VAULT_DIR` to choose the vault and `PYTHON` to choose the
interpreter (default `python3`).

## Deploy as a static site

```bash
cd apps/explorer
WHYKIT_VAULT_DIR=/path/to/vault npm run build
```

`dist/` is then a self-contained static site: `index.html` plus an `assets/`
folder holding the script, the stylesheet, the note bodies and the lint
findings. Routing uses the URL fragment (`#view=decisions`,
`#doc=06-decisions%2Fd-010-direct`) and every URL, the chunks included, is
relative, so you can serve the folder from a domain root, a sub-path such as a
GitHub Pages project site, or any static file server, without rewrite rules.
To preview the production build locally, run `npx vite preview`.

Opening `dist/index.html` straight from disk does not work: browsers do not
run module scripts loaded from `file://` and do not let such a page fetch its
note bodies. For that, build the single file described below.

The page carries a Content Security Policy in a `<meta>` tag:

```text
default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'
```

The build has no inline script or style element and loads nothing from
another origin. The dev server is left without the policy because it injects
inline scripts.

### Security headers

A `<meta>` policy cannot say who may embed the page in a frame
(`frame-ancestors`), so any site could show your Explorer inside its own page.
Explorer is read-only, which limits what that framing could trick someone into
doing, but if your host can set response headers, send the same policy as a
header with `frame-ancestors 'none'` added, plus two headers that stop content
sniffing and referrer leaks. If the vault is not public, put your
authentication in front of the same location.

**GitHub Pages** cannot set response headers. The `<meta>` policy still
applies; framing cannot be forbidden. If that matters, put a proxy or CDN that
can add headers in front of it, or use one of the hosts below.

**Netlify** and **Cloudflare Pages** read a `_headers` file from the published
folder. Create `dist/_headers` after each build (`vite build` empties `dist/`):

```text
/*
  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'
  X-Content-Type-Options: nosniff
  Referrer-Policy: no-referrer
  X-Frame-Options: DENY
```

On Netlify the same can live in `netlify.toml` instead, which survives
rebuilds:

```toml
[[headers]]
  for = "/*"
  [headers.values]
    Content-Security-Policy = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    X-Content-Type-Options = "nosniff"
    Referrer-Policy = "no-referrer"
    X-Frame-Options = "DENY"
```

**nginx**, in the `server` or `location` block that serves `dist/`. A
`location` block with any `add_header` of its own drops every header inherited
from `server`, so keep all four together:

```nginx
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Referrer-Policy "no-referrer" always;
add_header X-Frame-Options "DENY" always;
```

`X-Frame-Options` covers browsers too old for `frame-ancestors`. Check the
result with `curl -sI https://explorer.example.com/ | grep -i -E 'content-security|x-frame|nosniff|referrer'`.

### One file that opens from disk

```bash
cd apps/explorer
WHYKIT_VAULT_DIR=/path/to/vault npm run build:single
```

`dist-single/index.html` is the whole Explorer in one file: open it from disk
(`file://`), attach it to a ticket, or keep it next to an archived vault. The
script and stylesheet are inlined, and the summary, note bodies and lint
findings are embedded as gzip-compressed, base64-encoded data blocks that the
page decompresses in the browser, so nothing else is loaded and nothing is
fetched. The 5,000-note synthetic vault comes to about 2.5 MB. The build
refuses to write a file larger than 64 MB; set `WHYKIT_SINGLE_MAX_MB` to change
that limit.

Its policy differs from the static site's because the code is inline: it
allows exactly the inlined script and stylesheet by their SHA-256 hashes,
`connect-src 'none'`, and nothing else (`default-src 'none'`). Do not serve
this file behind the static-site header above: both policies would apply, and
`script-src 'self'` blocks the inline script. Serve `dist/` instead.

> [!WARNING]
> Private builds contain the full text of every document, including internal,
> confidential and restricted notes. Explorer has no access control. The private
> opt-in applies to single-file builds too; a file is easy to forward. Public
> builds use the filtered export by default. Review labels and content before
> publication, and use authentication for any private build.

## Checks

```bash
npm run check   # index, index smoke test, types, ESLint, unit tests, production build
npm run lint    # ESLint only (typescript-eslint, react-hooks, jsx-a11y)
npm run e2e     # Playwright end-to-end suite
```

The end-to-end suite builds three static sites: one from `examples/northline`,
one from a fresh `whykit init --minimal` vault for the empty states, and one from
the 5,000-note synthetic vault in `tests/synthetic_vault.py` (set
`WHYKIT_SYNTHETIC_NOTES` to change the size). It serves them with `vite preview`,
opens a single-file build of `examples/northline` from disk, and covers:

- navigation and focus management
- the search dialog's keyboard flow
- keyboard access to the graph: one Tab stop, arrow keys, Home and End,
  type-ahead, Space to select, Enter to open, also at 5,000 notes
- the D-009 → D-010 → D-011 supersession chain
- the timeline and freshness views, including policy windows and retired citations
- deep links: every filter, selection and search query survives a reload
- graph labels: wrapped and ellipsized by measured width, never past the node
- performance on the synthetic vault: time to interactive, time to a
  deep-linked note's text, search latency, view switches and graph highlight,
  each against a budget (`e2e/perf.spec.ts` prints the measured numbers)
- the static build: the CSP holds on every view with no violation, the build
  works from a sub-path (note bodies included), the note bodies and the lint
  findings are not in the entry bundle and the findings load only for Health,
  and the sensitive-content banner appears exactly when it should
- the single-file build opened from `file://`: one file, a hash-based policy
  with no violation on any view, no request for anything else, note text and
  search from the embedded data, and no serious axe violation
- printing a decision record: the supersession chain, the text and the
  registered evidence print in dark ink on white, without navigation
- empty states
- an axe-core scan that must report zero serious or critical violations
- a phone viewport

The suite runs only in Playwright's bundled Chromium. Install it once with:

```bash
npx playwright install chromium
```

Evidence rows show their effective sensitivity in the register and record cards. Private-view exposure notices include confidential/restricted evidence entries as well as notes. Public exports filter entries by their register/replacement floor; raw labeled tables are withheld.

## Claim views

The Explorer accepts existing v1 indexes and explicit v2 claim DTOs, rejects unknown versions and malformed claim relations, and renders the Python-computed state without a second state machine. C and dependent D views show both conflict sides, scope, validity and verification dates. Claim dependencies appear in the graph; source relations remain explicit in the inspector and freshness view. Receipts and host paths stay out of browser data. Source fragments are untrusted data.

The E2E builder creates separate `claims` and `claims-hidden` synthetic sites. They are test artifacts, not the final default public asset. Run E2E sequentially before `npm run check`, which restores the public Northline index.
