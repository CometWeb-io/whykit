/// <reference types="vite/client" />
// Declares the ambient modules Vite injects at build time, including the
// side-effect CSS import in main.tsx. Without this, `tsc --noEmit` fails with
// TS2882 on TypeScript 5.9+ while `vite build` itself succeeds — so the error
// only appears in CI, where npm ci resolves a newer patch than a stale
// node_modules locally.
