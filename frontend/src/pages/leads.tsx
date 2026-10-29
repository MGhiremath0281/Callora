import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import {
  Download,
  PhoneCall,
  Plus,
  Search,
  Upload,
  Users,
} from "lucide-react";

import { useBusiness } from "@/app/auth";
import { ImportWizard } from "@/components/domain/import-wizard";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ButtonLink } from "@/components/ui/button-link";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/form";
import { Pagination } from "@/components/ui/pagination";
import { PageHeader } from "@/components/ui/page-header";
import {
  EmptyState,
  ErrorState,
  InlineAlert,
  TableSkeleton,
} from "@/components/ui/states";
import {
  Table,
  TableWrap,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { LANGUAGE_NAMES } from "@/lib/constants";
import { formatDateTime, formatNumber, maskPhone } from "@/lib/utils";
import { leadSchema, type LeadFormValues } from "@/lib/validation";
import {
  createLead,
  listCalls,
  listLeads,
  listVoiceLeads,
} from "@/services/callora";
import { getCampaigns } from "@/services/campaigns";

const PAGE_SIZE = 20;

export default function LeadsPage() {
  const business = useBusiness();
  const queryClient = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [source, setSource] = useState("");
  const [consentFilter, setConsentFilter] = useState("all");
  const [outcomeFilter, setOutcomeFilter] = useState("all");
  const [campaignFilter, setCampaignFilter] = useState("all");
  const [page, setPage] = useState(1);
  const [addOpen, setAddOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  const campaignsQuery = useQuery({
    queryKey: ["campaigns", business.business_id],
    queryFn: () => getCampaigns(business.business_id),
  });
  const campaigns = campaignsQuery.data ?? [];

  const debouncedSearch = useDebouncedValue(search, 300);
  const debouncedSource = useDebouncedValue(source, 300);

  const leadsQuery = useQuery({
    queryKey: ["leads", debouncedSearch, debouncedSource, page],
    queryFn: () =>
      listLeads({
        query: debouncedSearch || undefined,
        source: debouncedSource || undefined,
        page,
        page_size: PAGE_SIZE,
      }),
    placeholderData: (previous) => previous,
  });

  const voiceQuery = useQuery({
    queryKey: ["voice-leads", "map"],
    queryFn: () => listVoiceLeads({ limit: 200 }),
    staleTime: 60_000,
  });

  const callsQuery = useQuery({
    queryKey: ["leads-latest-calls"],
    queryFn: () => listCalls({ limit: 200 }),
    staleTime: 30_000,
  });

  const profileByLead = useMemo(() => {
    const map = new Map<string, NonNullable<typeof voiceQuery.data>["items"][number]>();
    for (const profile of voiceQuery.data?.items ?? []) {
      map.set(profile.lead_id, profile);
    }
    return map;
  }, [voiceQuery.data]);

  const latestCallByLead = useMemo(() => {
    const map = new Map<string, NonNullable<typeof callsQuery.data>["items"][number]>();
    for (const call of callsQuery.data?.items ?? []) {
      if (!map.has(call.lead_id)) map.set(call.lead_id, call);
    }
    return map;
  }, [callsQuery.data]);

  const leads = leadsQuery.data?.items ?? [];
  const total = leadsQuery.data?.total ?? 0;

  const filteredLeads = useMemo(() => {
    return leads.filter((lead) => {
      const profile = profileByLead.get(lead.id);
      const latest = latestCallByLead.get(lead.id);
      const consentStatus = profile ? profile.consent?.status || "missing" : "missing";

      if (consentFilter !== "all" && consentStatus !== consentFilter) {
        return false;
      }
      if (outcomeFilter !== "all") {
        if (outcomeFilter === "uncalled" && latest) return false;
        if (outcomeFilter !== "uncalled" && (!latest || latest.status !== outcomeFilter)) return false;
      }
      if (campaignFilter !== "all") {
        const camp = campaigns.find((c) => c.id === campaignFilter);
        if (!camp || !camp.lead_ids.includes(lead.id)) return false;
      }
      return true;
    });
  }, [leads, profileByLead, latestCallByLead, consentFilter, outcomeFilter, campaignFilter, campaigns]);

  const hasFilters = Boolean(
    debouncedSearch ||
      debouncedSource ||
      consentFilter !== "all" ||
      outcomeFilter !== "all" ||
      campaignFilter !== "all",
  );

  const form = useForm<LeadFormValues>({
    resolver: zodResolver(leadSchema),
    defaultValues: {
      name: "",
      phone: "",
      source: "",
      email: "",
      language: business.default_language || "en",
      deal_value: "",
    },
  });

  const createMutation = useMutation({
    mutationFn: (values: LeadFormValues) =>
      createLead({
        name: values.name.trim(),
        phone: values.phone.trim(),
        source: values.source.trim(),
        email: values.email?.trim() || null,
        language: values.language || null,
        deal_value: values.deal_value?.trim() || null,
      }),
    onSuccess: (lead) => {
      toast.success(
        "Lead created",
        `${lead.name} has no consent evidence yet. Record consent before calling.`,
      );
      setAddOpen(false);
      form.reset();
      void queryClient.invalidateQueries({ queryKey: ["leads"] });
      void queryClient.invalidateQueries({ queryKey: ["voice-leads"] });
      void queryClient.invalidateQueries({ queryKey: ["voice-stats"] });
    },
    onError: (error) => {
      toast.error(
        "Lead was not created",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  function submitLead(values: LeadFormValues) {
    createMutation.mutate(values);
  }

  return (
    <div>
      <PageHeader
        title="Leads"
        description="Every lead is business-scoped. Consent, DND and call history are read from the voice backend, not guessed from the lead record."
        actions={
          <>
            <Button variant="outline" onClick={() => setImportOpen(true)}>
              <Upload className="h-4 w-4" aria-hidden /> Import CSV
            </Button>
            <Button onClick={() => setAddOpen(true)}>
              <Plus className="h-4 w-4" aria-hidden /> Add lead
            </Button>
          </>
        }
        meta={
          <>
            <Badge tone="neutral">{formatNumber(filteredLeads.length)} displayed</Badge>
            <Badge tone="accent">{business.name}</Badge>
            <span className="text-xs text-slate-400">
              Phone numbers are masked in list views.
            </span>
          </>
        }
      />

      <Card className="mb-4">
        <div className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center">
          <div className="relative flex-1">
            <Search
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
              aria-hidden
            />
            <Input
              aria-label="Search leads"
              placeholder="Search by name, phone or email"
              className="pl-9 text-xs"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setPage(1);
              }}
            />
          </div>
          <Input
            aria-label="Filter by source"
            placeholder="Source (e.g. website)"
            className="sm:w-36 text-xs"
            value={source}
            onChange={(event) => {
              setSource(event.target.value);
              setPage(1);
            }}
          />
          <Select
            aria-label="Consent filter"
            className="sm:w-32 text-xs"
            value={consentFilter}
            onChange={(e) => setConsentFilter(e.target.value)}
          >
            <option value="all">All consent</option>
            <option value="granted">Granted</option>
            <option value="missing">Missing / Unknown</option>
            <option value="revoked">Revoked</option>
          </Select>
          <Select
            aria-label="Call outcome filter"
            className="sm:w-32 text-xs"
            value={outcomeFilter}
            onChange={(e) => setOutcomeFilter(e.target.value)}
          >
            <option value="all">All outcomes</option>
            <option value="completed">Completed</option>
            <option value="busy">Busy</option>
            <option value="failed">Failed</option>
            <option value="uncalled">Not called yet</option>
          </Select>
          {campaigns.length > 0 && (
            <Select
              aria-label="Campaign filter"
              className="sm:w-36 text-xs"
              value={campaignFilter}
              onChange={(e) => setCampaignFilter(e.target.value)}
            >
              <option value="all">All campaigns</option>
              {campaigns.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name.length > 20 ? `${c.name.slice(0, 17)}...` : c.name}
                </option>
              ))}
            </Select>
          )}
          {hasFilters ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setSearch("");
                setSource("");
                setConsentFilter("all");
                setOutcomeFilter("all");
                setCampaignFilter("all");
                setPage(1);
              }}
            >
              Clear
            </Button>
          ) : null}
        </div>
      </Card>

      {voiceQuery.error || callsQuery.error ? (
        <div className="mb-4">
          <InlineAlert tone="warning" title="Some enrichment is unavailable">
            Lead records loaded, but consent/DND or recent-call data could not be loaded.
            Those cells are shown as unknown rather than guessed.
          </InlineAlert>
        </div>
      ) : null}

      <Card>
        {leadsQuery.isPending ? (
          <TableSkeleton rows={8} columns={6} />
        ) : leadsQuery.error ? (
          <ErrorState
            error={leadsQuery.error}
            title="Leads are unavailable"
            onRetry={() => void leadsQuery.refetch()}
          />
        ) : filteredLeads.length === 0 ? (
          hasFilters ? (
            <EmptyState
              icon={<Search className="h-5 w-5" aria-hidden />}
              title="No leads match"
              description="Change the search, source, consent or outcome filter."
              action={
                <Button
                  variant="outline"
                  onClick={() => {
                    setSearch("");
                    setSource("");
                    setConsentFilter("all");
                    setOutcomeFilter("all");
                    setCampaignFilter("all");
                  }}
                >
                  Clear filters
                </Button>
              }
            />
          ) : (
            <EmptyState
              icon={<Users className="h-5 w-5" aria-hidden />}
              title="No leads yet"
              description="Add one lead or import a CSV. Imported leads start without consent and are not callable until evidence is recorded."
              action={
                <div className="flex flex-wrap justify-center gap-2">
                  <Button variant="outline" onClick={() => setImportOpen(true)}>
                    <Download className="h-4 w-4" aria-hidden /> Import CSV
                  </Button>
                  <Button onClick={() => setAddOpen(true)}>
                    <Plus className="h-4 w-4" aria-hidden /> Add lead
                  </Button>
                </div>
              }
            />
          )
        ) : (
          <>
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Lead</TH>
                    <TH>Phone</TH>
                    <TH>Source</TH>
                    <TH>Language</TH>
                    <TH>Consent</TH>
                    <TH>DND</TH>
                    <TH>Latest call</TH>
                    <TH>Created</TH>
                    <TH className="text-right">Actions</TH>
                  </TR>
                </THead>
                <TBody>
                  {filteredLeads.map((lead) => {
                    const profile = profileByLead.get(lead.id);
                    const latest = latestCallByLead.get(lead.id);
                    const consentStatus = profile
                      ? profile.consent?.status || "missing"
                      : "unknown";
                    return (
                      <TR key={lead.id}>
                        <TD>
                          <Link
                            to={`/leads/${lead.id}`}
                            className="font-medium text-slate-900 hover:text-accent-700"
                          >
                            {lead.name}
                          </Link>
                          <p className="font-mono text-[11px] text-slate-400">
                            {lead.id}
                          </p>
                        </TD>
                        <TD className="font-mono text-xs">
                          {maskPhone(lead.phone)}
                        </TD>
                        <TD className="text-xs capitalize">{lead.source}</TD>
                        <TD className="text-xs">
                          {lead.language
                            ? LANGUAGE_NAMES[lead.language] || lead.language
                            : "—"}
                        </TD>
                        <TD>
                          <StatusBadge status={consentStatus} />
                          {consentStatus === "unknown" ? (
                            <p className="mt-0.5 text-[10px] text-slate-400">
                              profile not loaded
                            </p>
                          ) : null}
                        </TD>
                        <TD>
                          {!profile ? (
                            <StatusBadge status="unknown" />
                          ) : profile.dnd_active ? (
                            <Badge tone="danger">blocked</Badge>
                          ) : profile.do_not_call ? (
                            <Badge tone="warning">do not call</Badge>
                          ) : (
                            <Badge tone="success">clear</Badge>
                          )}
                        </TD>
                        <TD>
                          {latest ? (
                            <Link
                              to={`/calls/${latest.id}`}
                              className="inline-flex flex-col hover:text-accent-700"
                            >
                              <StatusBadge status={latest.status} />
                              <span className="mt-0.5 text-[11px] text-slate-400">
                                {formatDateTime(latest.created_at)}
                              </span>
                            </Link>
                          ) : (
                            <span className="text-xs text-slate-400">No recent call</span>
                          )}
                        </TD>
                        <TD className="whitespace-nowrap text-xs text-slate-500">
                          {formatDateTime(lead.created_at)}
                        </TD>
                        <TD>
                          <div className="flex items-center justify-end gap-1">
                            <ButtonLink
                              to={`/leads/${lead.id}`}
                              variant="ghost"
                              size="sm"
                            >
                              View
                            </ButtonLink>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={`Start call with ${lead.name}`}
                              title="Start a compliant outbound call"
                              onClick={() =>
                                navigate(`/calls?new=1&lead=${encodeURIComponent(lead.id)}`)
                              }
                            >
                              <PhoneCall className="h-3.5 w-3.5" />
                            </Button>
                          </div>
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
            </TableWrap>
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              total={total}
              onPageChange={setPage}
            />
            <div className="border-t border-slate-100 px-4 py-2 text-[11px] text-slate-400">
              Latest call and consent columns are derived from the 200 most recent call
              records and voice lead profiles returned by the API.
            </div>
          </>
        )}
      </Card>

      <Dialog
        open={addOpen}
        onClose={() => {
          if (!createMutation.isPending) {
            setAddOpen(false);
            form.reset();
          }
        }}
        title="Add a lead"
        description="The backend enforces business scoping and duplicate rules. Consent must be recorded separately."
        size="lg"
        footer={
          <>
            <Button
              variant="outline"
              onClick={() => {
                setAddOpen(false);
                form.reset();
              }}
              disabled={createMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={form.handleSubmit(submitLead)}
              loading={createMutation.isPending}
            >
              Create lead
            </Button>
          </>
        }
      >
        <form
          className="space-y-4"
          onSubmit={form.handleSubmit(submitLead)}
          noValidate
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label="Full name"
              htmlFor="lead-name"
              required
              error={form.formState.errors.name?.message}
            >
              <Input id="lead-name" placeholder="Ananya Rao" {...form.register("name")} />
            </Field>
            <Field
              label="Phone"
              htmlFor="lead-phone"
              required
              hint="Include the country code, e.g. +919876543210."
              error={form.formState.errors.phone?.message}
            >
              <Input
                id="lead-phone"
                inputMode="tel"
                placeholder="+91…"
                {...form.register("phone")}
              />
            </Field>
            <Field
              label="Source"
              htmlFor="lead-source"
              required
              hint="Free text used for filtering, e.g. website, referral, event."
              error={form.formState.errors.source?.message}
            >
              <Input
                id="lead-source"
                placeholder="website"
                {...form.register("source")}
              />
            </Field>
            <Field
              label="Email"
              htmlFor="lead-email"
              error={form.formState.errors.email?.message}
            >
              <Input
                id="lead-email"
                type="email"
                placeholder="optional"
                {...form.register("email")}
              />
            </Field>
            <Field
              label="Preferred language"
              htmlFor="lead-language"
              error={form.formState.errors.language?.message}
            >
              <select
                id="lead-language"
                className="field cursor-pointer"
                {...form.register("language")}
              >
                {(business.supported_languages || ["en"]).map((code) => (
                  <option key={code} value={code}>
                    {LANGUAGE_NAMES[code] || code}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label="Deal value"
              htmlFor="lead-deal-value"
              hint="Optional. Stored as supplied by the backend."
              error={form.formState.errors.deal_value?.message}
            >
              <Input
                id="lead-deal-value"
                inputMode="decimal"
                placeholder="50000"
                {...form.register("deal_value")}
              />
            </Field>
          </div>

          <InlineAlert tone="info" title="Consent is evidence, not a checkbox">
            A new lead is not callable until consent evidence is recorded on the lead page.
            The compliance gate evaluates it on every attempt.
          </InlineAlert>

          {createMutation.error ? (
            <InlineAlert tone="danger" title="The lead was not created">
              {createMutation.error instanceof Error
                ? createMutation.error.message
                : "The backend rejected the lead."}
            </InlineAlert>
          ) : null}
        </form>
      </Dialog>

      <ImportWizard
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={() => {
          void queryClient.invalidateQueries({ queryKey: ["leads"] });
        }}
      />
    </div>
  );
}
