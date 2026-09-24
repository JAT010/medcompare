import "@testing-library/jest-dom/vitest";

beforeEach(() => {
  sessionStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

afterEach(() => {
  sessionStorage.clear();
});
