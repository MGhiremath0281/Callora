import { describe, expect, it } from "vitest";

import { parseTranscript, transcriptAsText } from "./transcript";

describe("parseTranscript", () => {
  it("parses speaker-prefixed lines and normalises roles", () => {
    const turns = parseTranscript(
      "Assistant: Hello, is this Ada?\nCustomer: Yes, speaking.\n[00:05] AGENT - Great.",
    );

    expect(turns).toEqual([
      { speaker: "Agent", text: "Hello, is this Ada?" },
      { speaker: "Customer", text: "Yes, speaking." },
      { speaker: "Agent", text: "Great." },
    ]);
  });

  it("preserves unprefixed lines without inventing a speaker", () => {
    expect(parseTranscript("A line with no role.")).toEqual([
      { speaker: null, text: "A line with no role." },
    ]);
  });

  it("parses JSON turn arrays from the provider", () => {
    const turns = parseTranscript(
      JSON.stringify([
        { role: "agent", content: "Hello" },
        { role: "user", message: "Hi" },
        { speaker: "unknown", text: "Hmm" },
      ]),
    );

    expect(turns).toEqual([
      { speaker: "Agent", text: "Hello" },
      { speaker: "Customer", text: "Hi" },
      { speaker: "unknown", text: "Hmm" },
    ]);
  });

  it("parses an object with turns", () => {
    expect(
      parseTranscript(JSON.stringify({ turns: [{ text: "Only text" }] })),
    ).toEqual([{ speaker: null, text: "Only text" }]);
  });

  it("falls back to lines when JSON is malformed", () => {
    expect(parseTranscript("[not json")).toEqual([
      { speaker: null, text: "[not json" },
    ]);
  });

  it("exports text with speakers preserved", () => {
    expect(transcriptAsText("Agent: Hello\nCustomer: Hi")).toBe(
      "Agent: Hello\n\nCustomer: Hi",
    );
  });

  it("returns nothing for empty input", () => {
    expect(parseTranscript(null)).toEqual([]);
    expect(transcriptAsText("   ")).toBe("");
  });
});
