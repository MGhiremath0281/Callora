import { useQuery } from "@tanstack/react-query";
import { Check, Search, UserRound } from "lucide-react";
import { useEffect, useState } from "react";

import { Input } from "@/components/ui/form";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { cn, maskPhone } from "@/lib/utils";
import { listLeads, getLead } from "@/services/callora";
import type { Lead } from "@/types/api";

/**
 * Server-searched lead picker. It never invents a lead: only records returned
 * by `GET /leads` can be selected.
 */
export function LeadPicker({
  value,
  onChange,
  label = "Lead",
  error,
  disabled,
}: {
  value: string;
  onChange: (lead: Lead | null) => void;
  label?: string;
  error?: string;
  disabled?: boolean;
}) {
  const [query, setQuery] = useState("");
  const debounced = useDebouncedValue(query, 300);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Lead | null>(null);

  const { data, isFetching } = useQuery({
    queryKey: ["lead-picker", debounced],
    queryFn: () => listLeads({ query: debounced, page: 1, page_size: 8 }),
    enabled: open,
    staleTime: 30_000,
  });

  const { data: selectedLead } = useQuery({
    queryKey: ["lead", value],
    queryFn: () => getLead(value),
    enabled: Boolean(value) && !selected,
    staleTime: 5 * 60_000,
    retry: false,
  });

  useEffect(() => {
    if (value && !selected && selectedLead) setSelected(selectedLead);
  }, [value, selected, selectedLead]);

  useEffect(() => {
    if (!value) setSelected(null);
  }, [value]);

  const options = data?.items ?? [];

  return (
    <div>
      <label className="label" htmlFor="lead-picker-search">
        {label}
      </label>

      {selected ? (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-slate-300 bg-white px-3 py-2">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-slate-900">
              {selected.name} <span className="text-slate-400">·</span>{" "}
              <span className="font-mono text-xs">{maskPhone(selected.phone)}</span>
            </p>
            <p className="text-xs text-slate-500">
              {selected.source} · {selected.id}
            </p>
          </div>
          <button
            type="button"
            className="shrink-0 text-xs font-medium text-accent-600 hover:text-accent-700"
            onClick={() => {
              setSelected(null);
              onChange(null);
              setQuery("");
              setOpen(true);
            }}
          >
            Change
          </button>
        </div>
      ) : (
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
            aria-hidden
          />
          <Input
            id="lead-picker-search"
            className="pl-9"
            placeholder="Search leads by name or phone"
            value={query}
            disabled={disabled}
            autoComplete="off"
            onFocus={() => setOpen(true)}
            onChange={(event) => {
              setQuery(event.target.value);
              setOpen(true);
            }}
          />
          {open ? (
            <div className="absolute z-20 mt-1 w-full overflow-hidden rounded-lg border border-slate-200 bg-white shadow-pop">
              {isFetching && !options.length ? (
                <p className="px-3 py-3 text-xs text-slate-500">Searching…</p>
              ) : options.length === 0 ? (
                <p className="px-3 py-3 text-xs text-slate-500">
                  No leads match. Create the lead first in Leads.
                </p>
              ) : (
                <ul className="max-h-60 overflow-y-auto py-1" role="listbox">
                  {options.map((lead) => (
                    <li key={lead.id}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={value === lead.id}
                        className={cn(
                          "flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm hover:bg-slate-50",
                          value === lead.id && "bg-accent-50",
                        )}
                        onClick={() => {
                          setSelected(lead);
                          setOpen(false);
                          onChange(lead);
                        }}
                      >
                        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-500">
                          <UserRound className="h-3.5 w-3.5" aria-hidden />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-medium text-slate-800">
                            {lead.name}
                          </span>
                          <span className="block font-mono text-[11px] text-slate-500">
                            {maskPhone(lead.phone)} · {lead.source}
                          </span>
                        </span>
                        {value === lead.id ? (
                          <Check className="h-3.5 w-3.5 text-accent-600" aria-hidden />
                        ) : null}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <div className="border-t border-slate-100 px-3 py-1.5 text-right">
                <button
                  type="button"
                  className="text-xs text-slate-500 hover:text-slate-800"
                  onClick={() => setOpen(false)}
                >
                  Close
                </button>
              </div>
            </div>
          ) : null}
        </div>
      )}

      {error ? (
        <p role="alert" className="mt-1 text-xs font-medium text-rose-600">
          {error}
        </p>
      ) : (
        <p className="hint">Only leads that already exist in this workspace can be selected.</p>
      )}
    </div>
  );
}
