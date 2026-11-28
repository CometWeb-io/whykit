# Landing page media

Everything here comes from a real local run on 7 November 2026, not from
mockups. The vault in every file is `examples/northline`, a synthetic example
whose organisation, people and records are fictional.

- `explorer-demo.mp4`: screen recording of WhyKit Explorer (`apps/explorer`)
  at commit `73d6833` (WhyKit 0.4.0), built with
  `WHYKIT_EXPLORER_PRIVATE=1 npm run build` for `examples/northline` and served
  locally as static files. Captured with Playwright at 1280×800 in real time
  (about 24 s, no speed-up); the first 0.6 s of blank page was trimmed and the
  WebM was converted to H.264 MP4 with ffmpeg (`-crf 30 -movflags +faststart`).
  The private build was used because the default public index of this vault
  holds only 3 of its 57 notes (see the note below).
- `explorer-demo-poster.jpg`: the frame at 6 s of that recording.
- `explorer-home.jpg`, `explorer-chain.jpg`, `explorer-timeline.jpg`:
  screenshots of the same build at 1280×800, device scale factor 2, scaled to
  1600×1000 and saved as JPEG with ffmpeg. Review ages ("53 days") are relative
  to the capture date.
- `cli-session.txt`: an unedited terminal transcript of WhyKit 0.4.0 installed
  from PyPI into a temporary virtual environment, run on a copy of
  `examples/northline`. Commands are printed with shell quoting (`printf %q`).
  The hero animation and the terminal tabs on the page replay lines from this
  file; the hero animation is a staged illustration, not a recording.

Note: `whykit explorer-index --publication-preview` on `examples/northline`
withholds 54 of 57 notes, mostly as dependencies of `AGENTS.md` and
`OBSIDIAN.md` (each has an example wikilink inside inline code that the
publication check reports as unresolved, although `whykit lint` ignores it) and `templates/README.md` (no `sensitivity` key, so `internal`).
- `../og.jpg`: social preview image, a 1200×630 screenshot of this page's hero in its
  reduced-motion final state (the staged illustration above), taken with Playwright.
