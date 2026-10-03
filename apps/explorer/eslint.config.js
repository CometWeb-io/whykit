// Flat config. Type-aware rules run on the app, the unit tests and the
// end-to-end suite; plain scripts get the untyped baseline only.
import js from "@eslint/js";
import globals from "globals";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/", "dist-single/", "node_modules/", "src/generated/", "e2e/.build/", "test-results/", "playwright-report/"] },
  js.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    extends: [tseslint.configs.recommendedTypeChecked],
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    rules: {
      // Template literals with numbers are idiomatic in this UI (counts, ids).
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
    },
  },
  {
    files: ["src/**/*.tsx"],
    extends: [jsxA11y.flatConfigs.strict, reactHooks.configs.flat["recommended-latest"]],
    languageOptions: { globals: globals.browser },
    rules: {
      // Scrollable regions (tables, code blocks) must be keyboard-reachable
      // so their overflow can be scrolled without a mouse (WCAG 2.1.1).
      "jsx-a11y/no-noninteractive-tabindex": ["error", { roles: ["region"], tags: ["pre"] }],
    },
  },
  {
    files: ["scripts/**/*.mjs", "*.config.{js,ts}"],
    languageOptions: { globals: globals.node },
  },
  {
    files: ["e2e/**/*.ts", "tests/**/*.ts", "*.config.ts"],
    languageOptions: { globals: globals.node },
  },
  {
    // node:test registers top-level tests through returned promises that the
    // runner awaits; marking every one with `void` adds noise, not safety.
    files: ["tests/**/*.ts"],
    rules: { "@typescript-eslint/no-floating-promises": "off" },
  },
);
