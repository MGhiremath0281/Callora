import { describe, expect, it } from "vitest";

import {
  consentSchema,
  followupCompletionSchema,
  leadSchema,
} from "./validation";

describe("leadSchema", () => {
  const valid = {
    name: "Ada Lovelace",
    phone: "+919876543210",
    source: "Referral",
    email: "",
    language: "en",
    deal_value: "",
  };

  it("accepts a valid lead", () => {
    expect(leadSchema.safeParse(valid).success).toBe(true);
  });

  it("rejects a too-short name and an invalid phone", () => {
    const result = leadSchema.safeParse({
      ...valid,
      name: "A",
      phone: "not-a-number",
    });

    expect(result.success).toBe(false);
    if (!result.success) {
      const fields = result.error.issues.map((issue) => issue.path[0]);
      expect(fields).toContain("name");
      expect(fields).toContain("phone");
    }
  });

  it("rejects an invalid email when one is supplied", () => {
    const result = leadSchema.safeParse({ ...valid, email: "not-an-email" });

    expect(result.success).toBe(false);
  });

  it("accepts a numeric deal value with separators", () => {
    expect(
      leadSchema.safeParse({ ...valid, deal_value: "1,50,000" }).success,
    ).toBe(true);
  });
});

describe("consentSchema", () => {
  it("accepts backend consent statuses and channels", () => {
    const result = consentSchema.safeParse({
      status: "granted",
      channel: "voice",
      source: "Website form",
      granted_at: "2026-10-01T10:00",
      expires_at: "",
      note: "",
    });

    expect(result.success).toBe(true);
  });

  it("rejects an unknown channel", () => {
    expect(
      consentSchema.safeParse({ status: "granted", channel: "post" }).success,
    ).toBe(false);
  });
});

describe("followupCompletionSchema", () => {
  it("requires a meaningful outcome", () => {
    expect(followupCompletionSchema.safeParse({ outcome: "x" }).success).toBe(
      false,
    );
    expect(
      followupCompletionSchema.safeParse({ outcome: "Customer called back" })
        .success,
    ).toBe(true);
  });
});
