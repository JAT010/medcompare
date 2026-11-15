import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import CapabilityPanel from "./CapabilityPanel";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  localStorage.clear();
  window.history.replaceState({}, "", "/");
});

const REPORT = {
  capabilities: [
    {
      key: "standard_comparison",
      label: "Standard comparison",
      available: true,
      detail: "The server builds the 12-criterion matrix itself.",
      implementation: "comparison.service.build_comparison",
      required_integration: null,
    },
    {
      key: "public_internet_search",
      label: "Public internet research",
      available: false,
      detail: "No search provider client exists in this deployment.",
      implementation: null,
      required_integration: "A server-side search provider (for example Tavily or SerpAPI) with an API key.",
    },
  ],
};

it("renders available and missing capabilities from the server report", async () => {
  fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => REPORT });

  render(<CapabilityPanel />);

  expect(await screen.findByText("Standard comparison")).toBeInTheDocument();
  expect(screen.getAllByText("Available")).toHaveLength(1);
  expect(screen.getByText("Not available")).toBeInTheDocument();
  expect(
    screen.getByText(/Missing integration: A server-side search provider/),
  ).toBeInTheDocument();
  expect(screen.queryAllByRole("status")).toHaveLength(0);
});

it("reports when the capability report itself cannot be loaded", async () => {
  fetchMock.mockRejectedValue(new Error("connection refused"));

  render(<CapabilityPanel />);

  expect(await screen.findByText(/Cannot reach the live MedCompare server/)).toBeInTheDocument();
  expect(screen.getByText(/connection refused/)).toBeInTheDocument();
});

it("transitions to explicitly labelled demo capabilities after a live failure", async () => {
  fetchMock.mockRejectedValue(new Error("connection refused"));
  const user = userEvent.setup();

  render(<CapabilityPanel />);

  await user.click(await screen.findByRole("button", { name: "Enable demonstration mode" }));

  expect(await screen.findByText(/Demonstration mode is ON/)).toBeInTheDocument();
  expect(screen.getByText("Database-to-website consistency audit")).toBeInTheDocument();
  expect(screen.getByText("Registered hospital retrieval")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
