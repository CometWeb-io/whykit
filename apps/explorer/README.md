# WhyKit Explorer

A read-only browser for a WhyKit vault. It shows the decisions, the evidence
register, the review queue and history, the wikilink graph and the lint
results, all derived from the Markdown files. Explorer has no database and no
server-side code. It renders one JSON index that the Python package generates.

Explorer is optional and is not part of the data contract. A vault is complete
without it, and the CI job that builds it is non-blocking.

| View | What it shows |
|---|---|
| Home | Recent decisions, current sources of truth, integrity and open questions |
| Decisions | The append-only decision log, newest first, with supersession links |
| Timeline | How long each decision stayed in force: a bar from its date to the date of the decision that superseded it, filterable by status, owner and tag |
| Document | Any note, its registered evidence, links and backlinks; decision records also show their full supersession chain (for example D-009 → D-010 → D-011) |
| Evidence | The register, including retired rows, with a filter |
| Freshness | Each active source's access age against the `[evidence_access_age_days]` windows in `whykit.toml` (stale, due soon, fresh), and which notes in force still cite retired sources |
| Reviews | What is due within 30 days and the recorded review events |
| Graph | Notes grouped by workstream and connected by resolved wikilinks; click a note to select it and inspect its links, press Enter or double-click to open it |
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

Explorer renders `src/generated/vault.json`. `npm run index` writes that file
by calling the canonical Python implementation, so front matter and tables are
parsed in only one place:

```bash
uv run whykit explorer-index --root examples/northline --today 2026-09-17
```

The command prints the index as JSON. It contains every document's metadata and
body, the evidence IDs each note cites, the evidence, decision and review rows,
the freshness policy from `whykit.toml`, and the lint report. If the vault
has lint errors the command fails, and Explorer will not build from an invalid
vault. `npm run check:index` also rejects an index that includes an absolute
path from the build machine.

Set `WHYKIT_VAULT_DIR` to choose the vault and `PYTHON` to choose the
interpreter (default `python3`).

## Deploy as a static site

```bash
cd apps/explorer
WHYKIT_VAULT_DIR=/path/to/vault npm run build
```

`dist/` is then a self-contained static site. Routing uses the URL fragment
(`#view=decisions`, `#doc=06-decisions%2Fd-010-direct`) and asset URLs are
relative, so you can serve the folder from a domain root, a sub-path such as a
GitHub Pages project site, or any static file server, without rewrite rules.
To preview the production build locally, run `npx vite preview`.

> [!WARNING]
> The build contains the full text of every document in the vault,
> including `internal`, `confidential` and `restricted` notes. Explorer has no
> access control. Publish a build only when every document in the vault is
> meant to be public. Otherwise, serve it behind your own authentication.

## Checks

```bash
npm run check   # index, index smoke test, types, ESLint, unit tests, production build
npm run lint    # ESLint only (typescript-eslint, react-hooks, jsx-a11y)
npm run e2e     # Playwright end-to-end suite
```

The end-to-end suite builds three static sites: one from `examples/northline`,
one from a fresh `whykit init --minimal` vault for the empty states, and one from
the 5,000-note synthetic vault in `tests/synthetic_vault.py` (set
`WHYKIT_SYNTHETIC_NOTES` to change the size). It serves them with `vite preview`
and covers:

- navigation and focus management
- the search dialog's keyboard flow
- keyboard access to the graph
- the D-009 → D-010 → D-011 supersession chain
- the timeline and freshness views, including policy windows and retired citations
- deep links: every filter, selection and search query survives a reload
- graph labels: wrapped and ellipsized by measured width, never past the node
- performance on the synthetic vault: time to interactive, search latency,
  view switches and graph highlight, each against a budget (`e2e/perf.spec.ts`
  prints the measured numbers)
- empty states
- an axe-core scan that must report zero serious or critical violations
- a phone viewport

The suite runs only in Playwright's bundled Chromium. Install it once with:

```bash
npx playwright install chromium
```
