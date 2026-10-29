import { describe, expect, it } from "vitest";

import {
  CANONICAL_FIELDS,
  guessMapping,
  parseCsv,
  previewRows,
} from "./csv";

describe("parseCsv", () => {
  it("keeps commas and newlines inside quoted fields", () => {
    const parsed = parseCsv(
      'name,phone,source\n"Ada, Lovelace",+919876543210,"Referral\nPartner"\n',
    );

    expect(parsed.headers).toEqual(["name", "phone", "source"]);
    expect(parsed.rows).toEqual([
      ["Ada, Lovelace", "+919876543210", "Referral\nPartner"],
    ]);
    expect(parsed.delimiter).toBe(",");
  });

  it("detects semicolon and tab delimiters", () => {
    expect(parseCsv("name;phone\nAda;123").delimiter).toBe(";");
    expect(parseCsv("name\tphone\nAda\t123").delimiter).toBe("\t");
  });

  it("strips a UTF-8 BOM from the first header", () => {
    const parsed = parseCsv("\uFEFFname,phone\nAda,123");

    expect(parsed.headers[0]).toBe("name");
  });

  it("names empty headers instead of dropping them", () => {
    const parsed = parseCsv("name,,source\nAda,x,Referral");

    expect(parsed.headers).toEqual(["name", "Column 2", "source"]);
  });

  it("returns no rows for an empty file", () => {
    const parsed = parseCsv("");

    expect(parsed.headers).toEqual([]);
    expect(parsed.rows).toEqual([]);
  });
});

describe("guessMapping", () => {
  it("maps common CSV headers exactly once", () => {
    const mapping = guessMapping([
      "Full Name",
      "Mobile",
      "Channel",
      "Email",
      "Mobile 2",
    ]);

    expect(mapping).toMatchObject({
      "Full Name": "name",
      Mobile: "phone",
      Channel: "source",
      Email: "email",
    });
    expect(mapping["Mobile 2"]).toBe("ignore");
  });

  it("ignores unknown headers", () => {
    expect(guessMapping(["Mystery"])).toEqual({ Mystery: "ignore" });
  });
});

describe("previewRows", () => {
  it("maps only selected targets and preserves raw headers", () => {
    const parsed = parseCsv("Full Name,Mobile,Notes\nAda,123,hello");
    const rows = previewRows(
      parsed,
      { "Full Name": "name", Mobile: "phone", Notes: "ignore" },
      5,
    );

    expect(rows).toEqual([{ name: "Ada", phone: "123" }]);
  });
});

describe("canonical field contract", () => {
  it("requires only the fields the backend requires", () => {
    const required = CANONICAL_FIELDS.filter((field) => field.required).map(
      (field) => field.value,
    );

    expect(required).toEqual(["name", "phone", "source"]);
  });
});
