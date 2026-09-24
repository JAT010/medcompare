import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, setAuthToken, setUnauthorizedHandler } from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(handler: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => handler(String(input), init)),
  );
}

afterEach(() => {
  setAuthToken(null);
  setUnauthorizedHandler(null);
  vi.unstubAllGlobals();
});

describe("401 handling", () => {
  it("an unauthorized source probe (401) does not clear the session", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    setAuthToken("tok");

    stubFetch((url) => {
      if (url.includes("/api/v1/sources/hospital_api/test")) {
        return jsonResponse(
          {
            source_type: "hospital_api",
            requires_authorization: true,
            fetch_allowed: false,
            authorization: { status: "UNAUTHORIZED" },
            note: "offline probe only — no external request is made",
          },
          401,
        );
      }
      throw new Error("unexpected " + url);
    });

    await expect(api.testSource("hospital_api")).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("a dead session (401 from /auth/me) still clears the session", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    setAuthToken("expired");

    stubFetch((url) => {
      if (url.includes("/api/v1/auth/me")) {
        return jsonResponse({ detail: "Authentication required" }, 401);
      }
      throw new Error("unexpected " + url);
    });

    await expect(api.me()).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("a 401 from a normal protected endpoint clears the session", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    setAuthToken("expired");

    stubFetch((url) => {
      if (url.includes("/api/v1/users")) {
        return jsonResponse({ detail: "Authentication required" }, 401);
      }
      throw new Error("unexpected " + url);
    });

    await expect(api.listUsers()).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });
});
