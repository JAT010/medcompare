import { afterEach, expect, it } from "vitest";
import { DEMO_MODE_KEY, isDemoMode, setDemoMode } from "./demo";

afterEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});

it("honours the demo=1 URL flag", () => {
  window.history.replaceState({}, "", "/?demo=1");

  expect(isDemoMode()).toBe(true);
});

it("can disable demo mode even when the URL initially requested it", () => {
  window.history.replaceState({}, "", "/?demo=1");

  setDemoMode(false);

  expect(window.location.search).toBe("");
  expect(window.localStorage.getItem(DEMO_MODE_KEY)).toBe("0");
  expect(isDemoMode()).toBe(false);
});
