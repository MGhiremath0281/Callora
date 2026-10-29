/** Small, dependency-free CSV parser used only for the import preview. */
export type ParsedCsv = {
  headers: string[];
  rows: string[][];
  delimiter: string;
};

function detectDelimiter(firstLine: string) {
  const candidates = [",", ";", "\t"];
  let best = ",";
  let bestCount = -1;
  for (const candidate of candidates) {
    const count = firstLine.split(candidate).length - 1;
    if (count > bestCount) {
      best = candidate;
      bestCount = count;
    }
  }
  return best;
}

export function parseCsv(input: string): ParsedCsv {
  const text = input.replace(/^\uFEFF/, "");
  const firstLine = text.split(/\r?\n/, 1)[0] || "";
  const delimiter = detectDelimiter(firstLine);
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];

    if (quoted) {
      if (char === '"') {
        if (text[index + 1] === '"') {
          field += '"';
          index += 1;
        } else {
          quoted = false;
        }
      } else {
        field += char;
      }
      continue;
    }

    if (char === '"') {
      quoted = true;
    } else if (char === delimiter) {
      row.push(field);
      field = "";
    } else if (char === "\n" || char === "\r") {
      if (char === "\r" && text[index + 1] === "\n") index += 1;
      row.push(field);
      field = "";
      if (row.some((value) => value.trim() !== "")) rows.push(row);
      row = [];
    } else {
      field += char;
    }
  }

  row.push(field);
  if (row.some((value) => value.trim() !== "")) rows.push(row);

  const headers = (rows.shift() || []).map((header, index) => {
    const trimmed = header.trim();
    return trimmed || `Column ${index + 1}`;
  });

  return { headers, rows, delimiter };
}

export type CanonicalField = {
  value: string;
  label: string;
  required?: boolean;
};

export const CANONICAL_FIELDS: readonly CanonicalField[] = [
  { value: "name", label: "Name", required: true },
  { value: "phone", label: "Phone", required: true },
  { value: "source", label: "Source", required: true },
  { value: "email", label: "Email" },
  { value: "language", label: "Language" },
  { value: "deal_value", label: "Deal value" },
  { value: "last_contact_at", label: "Last contact at" },
  { value: "external_id", label: "External ID" },
  { value: "custom", label: "Custom field" },
  { value: "ignore", label: "Ignore" },
] as const;

const AUTO_MAP: Array<[RegExp, string]> = [
  [/^(full[\s_-]?)?name$|customer[\s_-]?name|lead[\s_-]?name/i, "name"],
  [/phone|mobile|contact[\s_-]?number|whatsapp/i, "phone"],
  [/source|channel|origin/i, "source"],
  [/e[\s_-]?mail/i, "email"],
  [/language|locale/i, "language"],
  [/budget|deal|value|amount/i, "deal_value"],
  [/last[\s_-]?contact/i, "last_contact_at"],
  [/external|lead[\s_-]?id/i, "external_id"],
];

export function guessMapping(headers: string[]) {
  const mapping: Record<string, string> = {};
  const used = new Set<string>();
  for (const header of headers) {
    const match = AUTO_MAP.find(
      ([pattern, field]) => pattern.test(header.trim()) && !used.has(field),
    );
    if (match) {
      mapping[header] = match[1];
      used.add(match[1]);
    } else {
      mapping[header] = "ignore";
    }
  }
  return mapping;
}

export function previewRows(
  parsed: ParsedCsv,
  mapping: Record<string, string>,
  limit = 5,
) {
  return parsed.rows.slice(0, limit).map((row) => {
    const mapped: Record<string, string> = {};
    parsed.headers.forEach((header, index) => {
      const target = mapping[header];
      if (!target || target === "ignore") return;
      mapped[target] = row[index]?.trim() ?? "";
    });
    return mapped;
  });
}
