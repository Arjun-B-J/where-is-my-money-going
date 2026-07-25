import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { FlatCompat } from "@eslint/eslintrc";

// ESLint 9 flat config. This file exists so `npm run lint` actually runs:
// without it, `next lint` drops into an interactive setup prompt, which is why
// the Makefile used to call it with `|| true` and never failed on a real problem.
const compat = new FlatCompat({
  baseDirectory: dirname(fileURLToPath(import.meta.url)),
});

const config = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    ignores: [".next/**", "node_modules/**", "playwright-report/**", "test-results/**"],
  },
  {
    rules: {
      // Unused variables are a real smell, but an unused function argument
      // named with a leading underscore is a deliberate signal.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
];

export default config;
