export type TranscriptTurn = {
  speaker: string | null;
  text: string;
};

const SPEAKER_LINE =
  /^(?:\[[^\]]*\]\s*)?(assistant|agent|ai|bot|user|customer|lead|caller|human|sales(?:\s+rep)?)\s*[:\-]\s*(.*)$/i;

function normaliseSpeaker(value: string) {
  const speaker = value.trim().toLowerCase();
  if (["assistant", "agent", "ai", "bot", "sales", "sales rep"].includes(speaker)) {
    return "Agent";
  }
  if (["user", "customer", "lead", "caller", "human"].includes(speaker)) {
    return "Customer";
  }
  return value.trim();
}

/**
 * The provider controls the transcript wire format. This parser accepts plain
 * speaker-prefixed text and JSON turn arrays, and otherwise preserves each line
 * verbatim without inventing speakers.
 */
export function parseTranscript(raw?: string | null): TranscriptTurn[] {
  if (!raw?.trim()) return [];

  const trimmed = raw.trim();
  if (trimmed.startsWith("[") || trimmed.startsWith("{")) {
    try {
      const parsed = JSON.parse(trimmed) as unknown;
      const candidates = Array.isArray(parsed)
        ? parsed
        : typeof parsed === "object" && parsed && Array.isArray((parsed as { turns?: unknown[] }).turns)
          ? (parsed as { turns: unknown[] }).turns
          : null;
      if (candidates) {
        const turns: TranscriptTurn[] = [];
        for (const candidate of candidates) {
          if (typeof candidate === "string") {
            turns.push({ speaker: null, text: candidate });
            continue;
          }
          if (!candidate || typeof candidate !== "object") continue;
          const record = candidate as Record<string, unknown>;
          const speaker =
            record.speaker ?? record.role ?? record.from ?? record.channel ?? null;
          const text =
            record.text ?? record.content ?? record.message ?? record.transcript ?? null;
          if (typeof text === "string" && text.trim()) {
            turns.push({
              speaker: typeof speaker === "string" ? normaliseSpeaker(speaker) : null,
              text: text.trim(),
            });
          }
        }
        if (turns.length) return turns;
      }
    } catch {
      // Fall through to the line parser.
    }
  }

  return trimmed
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const match = line.match(SPEAKER_LINE);
      if (match) {
        return { speaker: normaliseSpeaker(match[1]), text: match[2].trim() };
      }
      return { speaker: null, text: line };
    });
}

export function transcriptAsText(raw?: string | null) {
  return parseTranscript(raw)
    .map((turn) => (turn.speaker ? `${turn.speaker}: ${turn.text}` : turn.text))
    .join("\n\n");
}
