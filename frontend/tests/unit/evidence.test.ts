import { describe, it, expect } from "vitest";
import cases from "../../../tests/fixtures/synthetic/spans.json";
import { quoteAt } from "../../src/lib/api/evidence";
import { createApiClient } from "../../src/lib/api/client";

describe("shared code point contract", () => {
  for (const sample of cases)
    it(sample.quote, () =>
      expect(quoteAt(sample.text, sample.start, sample.end)).toBe(sample.quote),
    );
  it("rejects invalid bounds", () => {
    for (const [start, end] of [
      [-1, 2],
      [1, 1],
      [0, 100],
      [0.5, 2],
    ])
      expect(() => quoteAt("中文🧪", start, end)).toThrow(RangeError);
  });
  it("uses the generated health operation", async () => {
    const client = createApiClient("http://synthetic.invalid");
    client.use({
      onRequest() {
        return new Response(
          JSON.stringify({
            status: "ok",
            service: "clinical-data-api",
            version: "0.1.0",
          }),
          { headers: { "Content-Type": "application/json" } },
        );
      },
    });
    const { data, error } = await client.GET("/api/v1/health");
    expect(error).toBeUndefined();
    expect(data?.status).toBe("ok");
  });
});
