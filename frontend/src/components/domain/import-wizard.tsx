import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  FileSpreadsheet,
  Loader2,
  Upload,
} from "lucide-react";

import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Select } from "@/components/ui/form";
import { InlineAlert } from "@/components/ui/states";
import {
  Table,
  TableWrap,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui/table";
import {
  CANONICAL_FIELDS,
  guessMapping,
  parseCsv,
  previewRows,
  type ParsedCsv,
} from "@/lib/csv";
import { formatNumber } from "@/lib/utils";
import { getImport, getImportErrors, uploadImport } from "@/services/callora";

const TERMINAL_STATUSES = new Set(["completed", "failed", "partial"]);

/**
 * Three-step CSV import: choose a file, confirm the column mapping, then
 * upload. The backend is authoritative for validation, duplicate detection and
 * every row-level error.
 */
export function ImportWizard({
  open,
  onClose,
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  onImported?: () => void;
}) {
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [parsed, setParsed] = useState<ParsedCsv | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [step, setStep] = useState<"upload" | "map" | "result">("upload");
  const [readError, setReadError] = useState<string | null>(null);
  const [importId, setImportId] = useState<string | null>(null);

  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Choose a CSV file first.");
      const formData = new FormData();
      formData.append("file", file);
      formData.append(
        "mappings",
        JSON.stringify(
          Object.fromEntries(
            Object.entries(mapping).filter(([, target]) => target !== "ignore"),
          ),
        ),
      );
      return uploadImport(formData);
    },
    onSuccess: (summary) => {
      setImportId(summary.import_id);
      setStep("result");
      if (summary.created > 0) {
        void queryClient.invalidateQueries({ queryKey: ["leads"] });
        void queryClient.invalidateQueries({ queryKey: ["voice-leads"] });
        void queryClient.invalidateQueries({ queryKey: ["overview-calls"] });
        onImported?.();
      }
    },
  });

  const statusQuery = useQuery({
    queryKey: ["import", importId],
    queryFn: () => getImport(importId as string),
    enabled: Boolean(importId),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return 2000;
      return TERMINAL_STATUSES.has(data.status) ? false : 2000;
    },
  });

  const status = statusQuery.data;
  const effectiveStatus = status?.status || "";

  const errorsQuery = useQuery({
    queryKey: ["import-errors", importId],
    queryFn: () => getImportErrors(importId as string, { limit: 20 }),
    enabled:
      Boolean(importId) &&
      (TERMINAL_STATUSES.has(effectiveStatus) || (status?.rejected || 0) > 0),
  });

  const requiredMissing = useMemo(() => {
    const targets = new Set(Object.values(mapping));
    return CANONICAL_FIELDS.filter(
      (field) => field.required && !targets.has(field.value),
    );
  }, [mapping]);

  const duplicateTargets = useMemo(() => {
    const counts = new Map<string, string[]>();
    for (const [header, target] of Object.entries(mapping)) {
      if (target === "ignore" || target === "custom") continue;
      const list = counts.get(target) || [];
      list.push(header);
      counts.set(target, list);
    }
    return Array.from(counts.entries())
      .filter(([, headers]) => headers.length > 1)
      .map(([target, headers]) => ({ target, headers }));
  }, [mapping]);

  function reset() {
    setFile(null);
    setParsed(null);
    setMapping({});
    setStep("upload");
    setReadError(null);
    setImportId(null);
    uploadMutation.reset();
    if (inputRef.current) inputRef.current.value = "";
  }

  function close() {
    reset();
    onClose();
  }

  async function handleFile(selected: File | null) {
    setReadError(null);
    if (!selected) return;
    if (!selected.name.toLowerCase().endsWith(".csv")) {
      setReadError("Only .csv files are supported by the backend.");
      return;
    }
    if (selected.size > 5 * 1024 * 1024) {
      setReadError("Choose a CSV smaller than 5 MB.");
      return;
    }
    try {
      const text = await selected.text();
      const result = parseCsv(text);
      if (!result.headers.length || !result.rows.length) {
        setReadError("The file has no header row or no data rows.");
        return;
      }
      setFile(selected);
      setParsed(result);
      setMapping(guessMapping(result.headers));
      setStep("map");
      uploadMutation.reset();
    } catch {
      setReadError("The file could not be read as text. Export it as UTF-8 CSV and retry.");
    }
  }

  const preview = useMemo(
    () => (parsed ? previewRows(parsed, mapping, 5) : []),
    [parsed, mapping],
  );

  const canUpload =
    Boolean(file) &&
    step === "map" &&
    requiredMissing.length === 0 &&
    duplicateTargets.length === 0 &&
    !uploadMutation.isPending;

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Import leads from CSV"
      description="Map the columns once, preview the rows, then let the backend validate and persist the file."
      size="xl"
      footer={
        step === "upload" ? (
          <Button variant="outline" onClick={close}>
            Cancel
          </Button>
        ) : step === "map" ? (
          <>
            <Button
              variant="outline"
              onClick={() => {
                reset();
              }}
            >
              <ArrowLeft className="h-4 w-4" aria-hidden /> Choose another file
            </Button>
            <Button
              onClick={() => uploadMutation.mutate()}
              disabled={!canUpload}
              loading={uploadMutation.isPending}
            >
              <Upload className="h-4 w-4" aria-hidden /> Upload {parsed?.rows.length} rows
            </Button>
          </>
        ) : (
          <>
            <Button variant="outline" onClick={reset}>
              Import another file
            </Button>
            <Button onClick={close}>Done</Button>
          </>
        )
      }
    >
      <div className="mb-5 flex items-center gap-2">
        {[
          { id: "upload", label: "1 · Choose file" },
          { id: "map", label: "2 · Confirm mapping" },
          { id: "result", label: "3 · Review import" },
        ].map((item) => {
          const active = item.id === step;
          const done =
            (step === "map" && item.id === "upload") ||
            (step === "result" && item.id !== "result");
          return (
            <div key={item.id} className="flex flex-1 items-center gap-2">
              <span
                className={
                  active
                    ? "flex h-6 w-6 items-center justify-center rounded-full bg-accent-600 text-[11px] font-semibold text-white"
                    : done
                      ? "flex h-6 w-6 items-center justify-center rounded-full bg-emerald-500 text-white"
                      : "flex h-6 w-6 items-center justify-center rounded-full bg-slate-100 text-[11px] font-semibold text-slate-500"
                }
              >
                {done ? <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> : item.label[0]}
              </span>
              <span
                className={
                  active
                    ? "text-xs font-semibold text-slate-900"
                    : "text-xs text-slate-500"
                }
              >
                {item.label}
              </span>
              {item.id !== "result" ? (
                <span className="h-px flex-1 bg-slate-200" aria-hidden />
              ) : null}
            </div>
          );
        })}
      </div>

      {step === "upload" ? (
        <div>
          <label
            htmlFor="csv-file"
            className="flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-300 bg-slate-50/60 px-6 py-12 text-center transition hover:border-accent-400 hover:bg-accent-50/40"
          >
            <span className="flex h-11 w-11 items-center justify-center rounded-full bg-white text-accent-600 shadow-sm">
              <FileSpreadsheet className="h-5 w-5" aria-hidden />
            </span>
            <span className="mt-3 text-sm font-semibold text-slate-900">
              Choose a CSV file
            </span>
            <span className="mt-1 max-w-md text-xs leading-relaxed text-slate-500">
              The first row must be the header. UTF-8, comma, semicolon or tab separated,
              up to 5 MB. Need a template? The required columns are name, phone and source.
            </span>
            <span className="mt-3 rounded-lg bg-white px-3 py-1.5 text-xs font-medium text-accent-700 shadow-sm ring-1 ring-slate-200">
              Browse files
            </span>
            <input
              ref={inputRef}
              id="csv-file"
              type="file"
              accept=".csv,text/csv"
              className="sr-only"
              onChange={(event) => void handleFile(event.target.files?.[0] || null)}
            />
          </label>

          {readError ? (
            <div className="mt-4">
              <InlineAlert tone="danger" title="That file cannot be used">
                {readError}
              </InlineAlert>
            </div>
          ) : null}

          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            {[
              {
                title: "Duplicate safe",
                body: "The backend rejects duplicate phone numbers with a row-level reason.",
              },
              {
                title: "Consent aware",
                body: "Imported leads start without consent. The compliance gate will block calling until evidence is recorded.",
              },
              {
                title: "CRM sync",
                body: "When a CRM is connected, accepted leads are synchronized after persistence.",
              },
            ].map((item) => (
              <div key={item.title} className="rounded-lg border border-slate-200 p-3">
                <p className="text-xs font-semibold text-slate-800">{item.title}</p>
                <p className="mt-1 text-xs leading-relaxed text-slate-500">{item.body}</p>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {step === "map" && parsed ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="accent">{file?.name}</Badge>
            <Badge tone="neutral">{formatNumber(parsed.rows.length)} rows</Badge>
            <Badge tone="neutral">{parsed.headers.length} columns</Badge>
            <span className="text-xs text-slate-400">
              Nothing has been sent to the backend yet.
            </span>
          </div>

          {requiredMissing.length ? (
            <InlineAlert tone="warning" title="Required columns are not mapped">
              Map a column to {requiredMissing.map((field) => field.label).join(", ")}.
              The backend rejects rows without a name, phone and source.
            </InlineAlert>
          ) : null}

          {duplicateTargets.length ? (
            <InlineAlert tone="warning" title="Two columns target the same field">
              {duplicateTargets
                .map(({ target, headers }) => `${target} ← ${headers.join(", ")}`)
                .join(" · ")}
            </InlineAlert>
          ) : null}

          <TableWrap className="rounded-lg border border-slate-200">
            <Table className="min-w-[640px]">
              <THead>
                <TR>
                  <TH>CSV column</TH>
                  <TH>Sample value</TH>
                  <TH>Import as</TH>
                </TR>
              </THead>
              <TBody>
                {parsed.headers.map((header, index) => (
                  <TR key={header}>
                    <TD className="font-medium text-slate-800">{header}</TD>
                    <TD className="max-w-[260px] truncate font-mono text-xs text-slate-500">
                      {parsed.rows[0]?.[index] || "—"}
                    </TD>
                    <TD>
                      <Select
                        aria-label={`Map ${header}`}
                        value={mapping[header] || "ignore"}
                        onChange={(event) =>
                          setMapping((current) => ({
                            ...current,
                            [header]: event.target.value,
                          }))
                        }
                        className="h-8 w-44 py-1 text-xs"
                      >
                        {CANONICAL_FIELDS.map((field) => (
                          <option key={field.value} value={field.value}>
                            {field.label}
                            {field.required ? " *" : ""}
                          </option>
                        ))}
                      </Select>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </TableWrap>

          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
              Preview (first {preview.length} rows)
            </p>
            <TableWrap className="rounded-lg border border-slate-200">
              <Table className="min-w-[640px]">
                <THead>
                  <TR>
                    {CANONICAL_FIELDS.filter(
                      (field) =>
                        field.value !== "ignore" &&
                        field.value !== "custom" &&
                        Object.values(mapping).includes(field.value),
                    ).map((field) => (
                      <TH key={field.value}>{field.label}</TH>
                    ))}
                  </TR>
                </THead>
                <TBody>
                  {preview.map((row, index) => (
                    <TR key={index}>
                      {CANONICAL_FIELDS.filter(
                        (field) =>
                          field.value !== "ignore" &&
                          field.value !== "custom" &&
                          Object.values(mapping).includes(field.value),
                      ).map((field) => (
                        <TD key={field.value} className="max-w-[220px] truncate text-xs">
                          {row[field.value] || "—"}
                        </TD>
                      ))}
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          </div>

          <Field label="Custom fields" htmlFor="import-custom-note">
            <p id="import-custom-note" className="hint">
              Columns mapped to <span className="font-mono">Custom field</span> are stored
              as custom lead data by the backend. Columns mapped to{" "}
              <span className="font-mono">Ignore</span> are not persisted.
            </p>
          </Field>

          {uploadMutation.error ? (
            <InlineAlert tone="danger" title="The import could not start">
              {uploadMutation.error instanceof Error
                ? uploadMutation.error.message
                : "The backend rejected the upload."}
            </InlineAlert>
          ) : null}
        </div>
      ) : null}

      {step === "result" ? (
        <div className="space-y-4">
          {!status && uploadMutation.isPending ? (
            <div className="flex items-center justify-center gap-2 py-12 text-sm text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Uploading and
              validating…
            </div>
          ) : statusQuery.error ? (
            <InlineAlert tone="danger" title="Import status unavailable">
              {statusQuery.error instanceof Error
                ? statusQuery.error.message
                : "The import record could not be loaded."}
            </InlineAlert>
          ) : status ? (
            <>
              <InlineAlert
                tone={
                  effectiveStatus === "failed"
                    ? "danger"
                    : TERMINAL_STATUSES.has(effectiveStatus)
                      ? "success"
                      : "info"
                }
                title={
                  effectiveStatus === "failed"
                    ? "The import failed"
                    : TERMINAL_STATUSES.has(effectiveStatus)
                      ? "Import finished"
                      : "Import is processing"
                }
              >
                {status.error_message ||
                  (TERMINAL_STATUSES.has(effectiveStatus)
                    ? "The backend finished validating the file. Row-level results are below."
                    : "Large files are processed in the background. This panel refreshes automatically.")}
              </InlineAlert>

              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {[
                  { label: "Rows", value: status.total_rows },
                  { label: "Created", value: status.created },
                  { label: "Duplicates", value: status.duplicates },
                  { label: "Rejected", value: status.rejected },
                ].map((item) => (
                  <div key={item.label} className="rounded-lg border border-slate-200 p-3">
                    <dt className="text-xs text-slate-500">{item.label}</dt>
                    <dd className="mt-0.5 text-lg font-semibold tabular-nums text-slate-900">
                      {formatNumber(item.value)}
                    </dd>
                  </div>
                ))}
              </dl>

              <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                <StatusBadge status={status.status} />
                <span className="font-mono">{status.import_id}</span>
                {status.original_filename ? <span>· {status.original_filename}</span> : null}
                {!TERMINAL_STATUSES.has(effectiveStatus) ? (
                  <span className="inline-flex items-center gap-1 text-accent-600">
                    <Loader2 className="h-3 w-3 animate-spin" aria-hidden /> Polling
                  </span>
                ) : null}
              </div>

              {status.created > 0 ? (
                <InlineAlert tone="success" title="Leads are ready">
                  {formatNumber(status.created)} lead{status.created === 1 ? "" : "s"} were
                  created. They start without consent evidence, so record consent before
                  calling them.
                </InlineAlert>
              ) : null}

              {status.rejected > 0 || errorsQuery.data?.items.length ? (
                <div>
                  <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    <AlertTriangle className="h-3.5 w-3.5 text-amber-500" aria-hidden />
                    Row-level errors
                  </p>
                  {errorsQuery.isPending ? (
                    <p className="text-xs text-slate-500">Loading errors…</p>
                  ) : errorsQuery.data?.items.length ? (
                    <TableWrap className="rounded-lg border border-slate-200">
                      <Table className="min-w-[560px]">
                        <THead>
                          <TR>
                            <TH>Row</TH>
                            <TH>Field</TH>
                            <TH>Reason</TH>
                          </TR>
                        </THead>
                        <TBody>
                          {errorsQuery.data.items.map((error, index) => (
                            <TR key={`${error.row_number}-${index}`}>
                              <TD className="font-mono text-xs">{error.row_number}</TD>
                              <TD className="text-xs">{error.field || "row"}</TD>
                              <TD className="text-xs">
                                <span className="font-mono text-[11px] text-slate-400">
                                  {error.error_code}
                                </span>{" "}
                                {error.message}
                              </TD>
                            </TR>
                          ))}
                        </TBody>
                      </Table>
                    </TableWrap>
                  ) : (
                    <p className="text-xs text-slate-500">
                      No row-level errors were returned.
                    </p>
                  )}
                </div>
              ) : null}

              <p className="flex items-center gap-1.5 text-xs text-slate-400">
                <ArrowRight className="h-3 w-3" aria-hidden />
                Duplicate rows are reported, never silently overwritten.
              </p>
            </>
          ) : null}
        </div>
      ) : null}
    </Dialog>
  );
}
