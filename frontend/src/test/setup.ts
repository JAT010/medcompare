import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

// Vitest runs without injected globals, so Testing Library cannot register its
// own cleanup hook; tests must not leak DOM between cases.
afterEach(() => {
  cleanup();
});
