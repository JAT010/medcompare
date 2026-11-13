import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  compareHospitals,
  interpretPrompt,
  searchFacilities,
  toMessage,
} from "./client";
import type { ComparisonRequest } from "./types";

const fetchMock = vi.fn();

function reply(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
});

const REQUEST: ComparisonRequest = {
  scope: "standard_12",
  source: "medcompare_server",
  hospitals: [
    { type: "registered", facility_id: "11111111-1111-1111-1111-111111111111" },
    { type: "registered", facility_id: "22222222-2222-2222-2222-222222222222" },
  ],
};

describe("client", () => {
  it("posts the comparison to /api/v1/comparison", async () => {
    fetchMock.mockResolvedValue(reply(200, { rows: [] }));

    await compareHospitals(REQUEST);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/comparison");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toMatchObject({ scope: "standard_12" });
  });

  it("encodes the facility search query", async () => {
    fetchMock.mockResolvedValue(reply(200, { items: [], limit: 50, offset: 0, identity_note: "" }));

    await searchFacilities("north");

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/facilities?q=north");
  });

  it("turns a 501 detail payload into an ApiError with the required integration", async () => {
    fetchMock.mockResolvedValue(
      reply(501, {
        detail: {
          error: "public_research_not_configured",
          message: "MedCompare cannot research hospitals for you.",
          required_integration: "A server-side search provider with an API key.",
        },
      }),
    );

    const error = await compareHospitals({ ...REQUEST, source: "public_research" }).catch(
      (cause: unknown) => cause,
    );

    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(501);
    expect(apiError.errorCode).toBe("public_research_not_configured");
    expect(apiError.message).toContain("MedCompare cannot research hospitals");
    expect(apiError.requiredIntegration).toBe(
      "A server-side search provider with an API key.",
    );
  });

  it("joins FastAPI validation issues into one readable message", async () => {
    fetchMock.mockResolvedValue(
      reply(422, {
        detail: [{ msg: "Input should be a valid integer.", loc: ["body", "max_pages"] }],
      }),
    );

    const error = await interpretPrompt("").catch((cause: unknown) => cause);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toContain("Input should be a valid integer.");
  });

  it("falls back to a safe message when the server returns no body", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 500, json: async () => null });

    const error = await interpretPrompt("compare parking").catch((cause: unknown) => cause);

    expect((error as ApiError).message).toBe("Request failed with status 500.");
    expect(toMessage(error)).toBe("Request failed with status 500.");
  });
});
