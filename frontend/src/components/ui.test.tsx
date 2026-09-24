import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Badge, Card, Disclaimer, EmptyState, ErrorState, EvidenceValue, Loading } from "./ui";

describe("ui components", () => {
  it("Badge renders children", () => {
    render(<Badge tone="ok">SIMULATED</Badge>);
    expect(screen.getByTestId("badge")).toHaveTextContent("SIMULATED");
  });

  it("Card with title", () => {
    render(
      <Card title="Hospitals">
        <p>body</p>
      </Card>,
    );
    expect(screen.getByRole("heading", { name: "Hospitals" })).toBeInTheDocument();
  });

  it("Loading has status role", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading…");
  });

  it("EmptyState", () => {
    render(<EmptyState message="Nothing here." />);
    expect(screen.getByTestId("empty")).toHaveTextContent("Nothing here.");
  });

  it("ErrorState with status", () => {
    render(<ErrorState message="Forbidden" status={403} />);
    expect(screen.getByRole("alert")).toHaveTextContent("HTTP 403");
    expect(screen.getByRole("alert")).toHaveTextContent("Forbidden");
  });

  it("EvidenceValue distinguishes UNKNOWN / NO / YES / literal", () => {
    render(
      <>
        <EvidenceValue value="UNKNOWN" />
        <EvidenceValue value="NO" />
        <EvidenceValue value="YES" />
        <EvidenceValue value="42 beds" />
        <EvidenceValue value={undefined} />
      </>,
    );
    expect(screen.getAllByTestId("unknown")).toHaveLength(2);
    expect(screen.getByTestId("no")).toBeInTheDocument();
    expect(screen.getByTestId("yes")).toBeInTheDocument();
    expect(screen.getByTestId("value")).toHaveTextContent("42 beds");
  });

  it("Disclaimer contains demo and credential language", () => {
    render(<Disclaimer />);
    const el = screen.getByTestId("disclaimer");
    expect(el).toHaveTextContent(/fictional/i);
    expect(el).toHaveTextContent(/credentials/i);
    expect(el).toHaveTextContent(/no ranking/i);
  });
});
