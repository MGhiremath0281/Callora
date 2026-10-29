import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";
import {
  Bell,
  CheckCircle2,
  ClipboardCopy,
  Database,
  KeyRound,
  Link2,
  LogOut,
  Plus,
  RefreshCw,
  Save,
  ShieldAlert,
  ShieldCheck,
  Users,
  Webhook,
} from "lucide-react";

import { useAuth, useBusiness } from "@/app/auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ButtonLink } from "@/components/ui/button-link";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import {
  Checkbox,
  Field,
  Input,
  Select,
  Switch,
  Textarea,
} from "@/components/ui/form";
import { PageHeader } from "@/components/ui/page-header";
import {
  EmptyState,
  ErrorState,
  InlineAlert,
  Skeleton,
} from "@/components/ui/states";
import { Tabs } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { API_BASE } from "@/lib/api";
import {
  CALLING_DAYS,
  LANGUAGE_NAMES,
  SUPPORTED_LANGUAGES,
  TIMEZONES,
} from "@/lib/constants";
import { formatDateTime } from "@/lib/utils";
import {
  createIntegration,
  listCrmConnections,
  listIntegrations,
  saveCrmConnection,
  updateBusiness,
} from "@/services/callora";
import type { Business, CRMConnection } from "@/types/api";

const phonePattern = /^\+?[0-9][0-9\s-]{5,20}$/;

const workspaceSchema = z
  .object({
    name: z.string().trim().min(1, "Workspace name is required.").max(255),
    timezone: z.string().min(1),
    default_language: z.string().min(1),
    supported_languages: z
      .array(z.enum(SUPPORTED_LANGUAGES))
      .min(1, "Select at least one language."),
  })
  .superRefine((values, ctx) => {
    if (!values.supported_languages.includes(values.default_language as "en" | "hi" | "kn")) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["default_language"],
        message: "The default language must be one of the supported languages.",
      });
    }
  });

type WorkspaceForm = z.infer<typeof workspaceSchema>;

const callingSchema = z
  .object({
    calling_window_start: z.number().int().min(0).max(23),
    calling_window_end: z.number().int().min(0).max(23),
    calling_days: z.array(z.number().int().min(0).max(6)).min(1, "Select at least one day."),
    max_calls_per_lead_per_day: z.number().int().min(1).max(50),
    max_calls_per_business_per_day: z.number().int().min(1).max(100000),
    max_concurrent_calls: z.number().int().min(1).max(1000),
    require_consent: z.boolean(),
    dnd_enabled: z.boolean(),
  })
  .superRefine((values, ctx) => {
    if (values.calling_window_start === values.calling_window_end) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["calling_window_end"],
        message: "The window must end at a different hour than it starts.",
      });
    }
  });

type CallingForm = z.infer<typeof callingSchema>;

const handoffSchema = z
  .object({
    handoff_target_number: z.string().trim().max(32).optional(),
    warm_transfer_enabled: z.boolean(),
    warm_transfer_number: z.string().trim().max(32).optional(),
  })
  .superRefine((values, ctx) => {
    if (
      values.handoff_target_number &&
      !phonePattern.test(values.handoff_target_number)
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["handoff_target_number"],
        message: "Enter a valid phone number with country code.",
      });
    }
    if (values.warm_transfer_number && !phonePattern.test(values.warm_transfer_number)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["warm_transfer_number"],
        message: "Enter a valid phone number with country code.",
      });
    }
    if (values.warm_transfer_enabled && !values.warm_transfer_number) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["warm_transfer_number"],
        message: "Warm transfer requires a destination number.",
      });
    }
  });

type HandoffForm = z.infer<typeof handoffSchema>;

type TabId = "workspace" | "calling" | "handoffs" | "integrations" | "account";

export default function SettingsPage() {
  const business = useBusiness();
  const { refreshBusiness, logout, apiKey } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const initialTab = (searchParams.get("tab") as TabId) || "workspace";
  const [tab, setTab] = useState<TabId>(
    ["workspace", "calling", "handoffs", "integrations", "account"].includes(initialTab)
      ? initialTab
      : "workspace",
  );

  const workspaceForm = useForm<WorkspaceForm>({
    resolver: zodResolver(workspaceSchema),
    defaultValues: workspaceDefaults(business),
  });

  const callingForm = useForm<CallingForm>({
    resolver: zodResolver(callingSchema),
    defaultValues: callingDefaults(business),
  });

  const handoffForm = useForm<HandoffForm>({
    resolver: zodResolver(handoffSchema),
    defaultValues: handoffDefaults(business),
  });

  useEffect(() => {
    workspaceForm.reset(workspaceDefaults(business));
    callingForm.reset(callingDefaults(business));
    handoffForm.reset(handoffDefaults(business));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [business]);

  const workspaceMutation = useMutation({
    mutationFn: (values: WorkspaceForm) => updateBusiness(values),
    onSuccess: (updated) => {
      toast.success("Workspace settings saved");
      workspaceForm.reset(workspaceDefaults(updated));
      void refreshBusiness();
      void queryClient.invalidateQueries({ queryKey: ["business"] });
    },
    onError: (error) => {
      toast.error(
        "Workspace settings were not saved",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const callingMutation = useMutation({
    mutationFn: (values: CallingForm) => updateBusiness(values),
    onSuccess: (updated) => {
      toast.success(
        "Calling policy saved",
        "The compliance gate applies the new policy to future attempts.",
      );
      callingForm.reset(callingDefaults(updated));
      void refreshBusiness();
    },
    onError: (error) => {
      toast.error(
        "Calling policy was not saved",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const handoffMutation = useMutation({
    mutationFn: (values: HandoffForm) =>
      updateBusiness({
        handoff_target_number: values.handoff_target_number || null,
        warm_transfer_enabled: values.warm_transfer_enabled,
        warm_transfer_number: values.warm_transfer_number || null,
      }),
    onSuccess: (updated) => {
      toast.success("Handoff settings saved");
      handoffForm.reset(handoffDefaults(updated));
      void refreshBusiness();
    },
    onError: (error) => {
      toast.error(
        "Handoff settings were not saved",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const languages = workspaceForm.watch("supported_languages");
  const defaultLanguage = workspaceForm.watch("default_language");
  const selectedDays = callingForm.watch("calling_days");

  function toggleLanguage(code: string, checked: boolean) {
    const current = workspaceForm.getValues("supported_languages");
    const next = checked
      ? [...current, code as "en" | "hi" | "kn"]
      : current.filter((value) => value !== code);
    workspaceForm.setValue("supported_languages", next, { shouldDirty: true });
    if (!next.includes(defaultLanguage as "en" | "hi" | "kn") && next.length) {
      workspaceForm.setValue("default_language", next[0], { shouldDirty: true });
    }
  }

  const maskedKey = useMemo(() => {
    if (!apiKey) return "—";
    if (apiKey.length <= 8) return "•".repeat(apiKey.length);
    return `${apiKey.slice(0, 4)}${"•".repeat(Math.min(24, apiKey.length - 8))}${apiKey.slice(-4)}`;
  }, [apiKey]);

  return (
    <div>
      <PageHeader
        title="Settings"
        description="Workspace policy, calling windows, handoff defaults, integrations and account access. Changes are sent to the backend and audited where it records them."
        actions={
          <Button
            variant="outline"
            onClick={() => {
              void refreshBusiness().then((updated) => {
                if (!updated) {
                  toast.error(
                    "Workspace could not be reloaded",
                    "Check the backend connection and try again.",
                  );
                  return;
                }
                workspaceForm.reset(workspaceDefaults(updated));
                callingForm.reset(callingDefaults(updated));
                handoffForm.reset(handoffDefaults(updated));
                toast.success("Workspace reloaded from the API");
              });
            }}
          >
            <RefreshCw className="h-4 w-4" aria-hidden /> Reload
          </Button>
        }
        meta={
          <>
            <Badge tone={business.is_active ? "success" : "warning"}>
              Agent {business.is_active ? "active" : "inactive"}
            </Badge>
            <Badge tone={business.provider_configured ? "success" : "danger"}>
              Provider {business.provider_configured ? "configured" : "missing"}
            </Badge>
            <span className="font-mono text-[11px] text-slate-400">
              {business.business_id}
            </span>
          </>
        }
      />

      <Tabs
        items={[
          { id: "workspace", label: "Workspace" },
          { id: "calling", label: "Calling & compliance" },
          { id: "handoffs", label: "Handoffs" },
          { id: "integrations", label: "Integrations" },
          { id: "account", label: "Account & API" },
        ]}
        value={tab}
        onChange={(next) => setTab(next as TabId)}
        className="mb-4"
      />

      {tab === "workspace" ? (
        <div className="grid gap-4 xl:grid-cols-3">
          <Card className="xl:col-span-2">
            <CardHeader
              title="Workspace profile"
              description="Displayed in the sidebar and used for business-scoped defaults."
            />
            <CardBody>
              <form
                className="space-y-4"
                onSubmit={workspaceForm.handleSubmit((values) =>
                  workspaceMutation.mutate(values),
                )}
                noValidate
              >
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field
                    label="Workspace name"
                    htmlFor="settings-name"
                    required
                    error={workspaceForm.formState.errors.name?.message}
                  >
                    <Input
                      id="settings-name"
                      {...workspaceForm.register("name")}
                    />
                  </Field>
                  <Field
                    label="Timezone"
                    htmlFor="settings-timezone"
                    required
                    error={workspaceForm.formState.errors.timezone?.message}
                  >
                    <Select id="settings-timezone" {...workspaceForm.register("timezone")}>
                      {TIMEZONES.map((timezone) => (
                        <option key={timezone} value={timezone}>
                          {timezone}
                        </option>
                      ))}
                    </Select>
                  </Field>
                </div>

                <Field
                  label="Supported languages"
                  required
                  error={workspaceForm.formState.errors.supported_languages?.message}
                  hint="The agent can switch between selected languages."
                >
                  <div className="grid gap-2 sm:grid-cols-3">
                    {SUPPORTED_LANGUAGES.map((code) => (
                      <Checkbox
                        key={code}
                        label={LANGUAGE_NAMES[code]}
                        checked={languages.includes(code)}
                        onChange={(event) => toggleLanguage(code, event.target.checked)}
                      />
                    ))}
                  </div>
                </Field>

                <Field
                  label="Default language"
                  htmlFor="settings-default-language"
                  required
                  error={workspaceForm.formState.errors.default_language?.message}
                >
                  <Select
                    id="settings-default-language"
                    {...workspaceForm.register("default_language")}
                  >
                    {languages.map((code) => (
                      <option key={code} value={code}>
                        {LANGUAGE_NAMES[code] || code}
                      </option>
                    ))}
                  </Select>
                </Field>

                {workspaceMutation.error ? (
                  <InlineAlert tone="danger" title="Workspace settings were not saved">
                    {workspaceMutation.error instanceof Error
                      ? workspaceMutation.error.message
                      : "The backend rejected the update."}
                  </InlineAlert>
                ) : null}

                <div className="flex justify-end">
                  <Button type="submit" loading={workspaceMutation.isPending}>
                    <Save className="h-4 w-4" aria-hidden /> Save workspace
                  </Button>
                </div>
              </form>
            </CardBody>
          </Card>

          <div className="space-y-4">
            <Card>
              <CardHeader title="Provider and agent" />
              <CardBody className="space-y-3 text-sm">
                <Row label="Provider configured" value={business.provider_configured ? "Yes" : "No"} ok={business.provider_configured} />
                <Row label="Outbound enabled" value={business.outbound_calls_enabled ? "Yes" : "No"} ok={business.outbound_calls_enabled} />
                <Row label="Agent active" value={business.is_active ? "Yes" : "No"} ok={business.is_active} />
                <Row
                  label="Bolna agent id"
                  value={business.bolna_agent_id || "Not set"}
                  mono
                />
                <ButtonLink to="/agents" variant="outline" size="sm" className="w-full">
                  Open Agent Studio
                </ButtonLink>
                <p className="text-xs leading-relaxed text-slate-500">
                  Provider credentials live on the server and are never exposed to the browser.
                  The UI can only observe whether the server reports them as configured.
                </p>
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Workspace metadata" />
              <CardBody className="space-y-2 text-xs">
                <Row label="Business id" value={business.business_id} mono />
                <Row label="Created" value={formatDateTime(business.created_at)} />
                <Row label="Updated" value={formatDateTime(business.updated_at)} />
                <Row label="Consent required" value={business.require_consent ? "Yes" : "No"} />
                <Row label="Quotas disabled" value={business.call_quotas_disabled ? "Yes" : "No"} />
              </CardBody>
            </Card>
          </div>
        </div>
      ) : null}

      {tab === "calling" ? (
        <div className="grid gap-4 xl:grid-cols-3">
          <Card className="xl:col-span-2">
            <CardHeader
              title="Calling window and quotas"
              description="The backend enforces these values at the compliance gate. The browser does not pre-empt the decision."
            />
            <CardBody>
              <form
                className="space-y-5"
                onSubmit={callingForm.handleSubmit((values) =>
                  callingMutation.mutate(values),
                )}
                noValidate
              >
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field
                    label="Window starts (hour)"
                    htmlFor="window-start"
                    hint={`${business.timezone} · 24-hour clock`}
                    error={callingForm.formState.errors.calling_window_start?.message}
                  >
                    <Input
                      id="window-start"
                      type="number"
                      min={0}
                      max={23}
                      {...callingForm.register("calling_window_start", {
                        valueAsNumber: true,
                      })}
                    />
                  </Field>
                  <Field
                    label="Window ends (hour)"
                    htmlFor="window-end"
                    hint="Calls are blocked outside this window."
                    error={callingForm.formState.errors.calling_window_end?.message}
                  >
                    <Input
                      id="window-end"
                      type="number"
                      min={0}
                      max={23}
                      {...callingForm.register("calling_window_end", {
                        valueAsNumber: true,
                      })}
                    />
                  </Field>
                </div>

                <Field
                  label="Calling days"
                  required
                  error={callingForm.formState.errors.calling_days?.message}
                  hint="Based on the business timezone. Monday is index 0 in the API."
                >
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    {CALLING_DAYS.map((day) => (
                      <Checkbox
                        key={day.value}
                        label={day.label}
                        checked={selectedDays.includes(day.value)}
                        onChange={(event) => {
                          const current = callingForm.getValues("calling_days");
                          const next = event.target.checked
                            ? [...current, day.value]
                            : current.filter((value) => value !== day.value);
                          callingForm.setValue("calling_days", next, {
                            shouldDirty: true,
                          });
                        }}
                      />
                    ))}
                  </div>
                </Field>

                <div className="grid gap-4 sm:grid-cols-3">
                  <Field
                    label="Calls per lead / day"
                    htmlFor="quota-lead"
                    error={
                      callingForm.formState.errors.max_calls_per_lead_per_day?.message
                    }
                  >
                    <Input
                      id="quota-lead"
                      type="number"
                      min={1}
                      {...callingForm.register("max_calls_per_lead_per_day", {
                        valueAsNumber: true,
                      })}
                    />
                  </Field>
                  <Field
                    label="Calls per business / day"
                    htmlFor="quota-business"
                    error={
                      callingForm.formState.errors.max_calls_per_business_per_day
                        ?.message
                    }
                  >
                    <Input
                      id="quota-business"
                      type="number"
                      min={1}
                      {...callingForm.register("max_calls_per_business_per_day", {
                        valueAsNumber: true,
                      })}
                    />
                  </Field>
                  <Field
                    label="Concurrent calls"
                    htmlFor="quota-concurrent"
                    error={callingForm.formState.errors.max_concurrent_calls?.message}
                  >
                    <Input
                      id="quota-concurrent"
                      type="number"
                      min={1}
                      {...callingForm.register("max_concurrent_calls", {
                        valueAsNumber: true,
                      })}
                    />
                  </Field>
                </div>

                <div className="space-y-2">
                  <Switch
                    label="Require consent evidence"
                    description="Blocks calls without current granted consent. Turning this off weakens compliance and is audited."
                    checked={callingForm.watch("require_consent")}
                    onChange={(checked) =>
                      callingForm.setValue("require_consent", checked, {
                        shouldDirty: true,
                      })
                    }
                  />
                  <Switch
                    label="Enforce DND registry"
                    description="Blocks calls to numbers on the business or global DND list."
                    checked={callingForm.watch("dnd_enabled")}
                    onChange={(checked) =>
                      callingForm.setValue("dnd_enabled", checked, {
                        shouldDirty: true,
                      })
                    }
                  />
                </div>

                {business.call_quotas_disabled ? (
                  <InlineAlert tone="warning" title="Quotas are disabled server-side">
                    The backend reports that quota limits are turned off for this environment.
                    Saving values here does not override that deployment setting.
                  </InlineAlert>
                ) : null}

                {callingMutation.error ? (
                  <InlineAlert tone="danger" title="Calling policy was not saved">
                    {callingMutation.error instanceof Error
                      ? callingMutation.error.message
                      : "The backend rejected the update."}
                  </InlineAlert>
                ) : null}

                <div className="flex justify-end">
                  <Button type="submit" loading={callingMutation.isPending}>
                    <Save className="h-4 w-4" aria-hidden /> Save policy
                  </Button>
                </div>
              </form>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Compliance reference" />
            <CardBody className="space-y-3 text-sm text-slate-600">
              <p className="flex items-start gap-2">
                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
                Every attempt is evaluated by the backend gate, including dry runs.
              </p>
              <p className="flex items-start gap-2">
                <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
                Disabling consent or DND enforcement can make outreach non-compliant. Do it
                only when the deployment is explicitly exempt.
              </p>
              <ButtonLink to="/compliance" variant="outline" size="sm" className="w-full">
                Open compliance & audit
              </ButtonLink>
            </CardBody>
          </Card>
        </div>
      ) : null}

      {tab === "handoffs" ? (
        <div className="grid gap-4 xl:grid-cols-3">
          <Card className="xl:col-span-2">
            <CardHeader
              title="Human handoff defaults"
              description="Used when an agent escalates or an operator queues a callback. Provider support varies."
            />
            <CardBody>
              <form
                className="space-y-4"
                onSubmit={handoffForm.handleSubmit((values) =>
                  handoffMutation.mutate(values),
                )}
                noValidate
              >
                <Field
                  label="Default handoff target"
                  htmlFor="handoff-target"
                  error={
                    handoffForm.formState.errors.handoff_target_number?.message
                  }
                  hint="Leave blank to rely on the provider or per-handoff target."
                >
                  <Input
                    id="handoff-target"
                    placeholder="+91…"
                    {...handoffForm.register("handoff_target_number")}
                  />
                </Field>

                <Switch
                  label="Enable warm transfer"
                  description="The agent can ask the provider to transfer the live call to a human."
                  checked={handoffForm.watch("warm_transfer_enabled")}
                  onChange={(checked) =>
                    handoffForm.setValue("warm_transfer_enabled", checked, {
                      shouldDirty: true,
                    })
                  }
                />

                <Field
                  label="Warm transfer number"
                  htmlFor="warm-transfer-number"
                  required={handoffForm.watch("warm_transfer_enabled")}
                  error={
                    handoffForm.formState.errors.warm_transfer_number?.message
                  }
                  hint="Include country code. Provider-side transfer must be supported."
                >
                  <Input
                    id="warm-transfer-number"
                    placeholder="+91…"
                    {...handoffForm.register("warm_transfer_number")}
                  />
                </Field>

                <InlineAlert tone="warning" title="A transfer can fail">
                  Callora records the handoff request and its status. It does not claim a human
                  answered unless the provider reports that outcome. Test the provider
                  configuration before relying on warm transfer in production.
                </InlineAlert>

                {handoffMutation.error ? (
                  <InlineAlert tone="danger" title="Handoff settings were not saved">
                    {handoffMutation.error instanceof Error
                      ? handoffMutation.error.message
                      : "The backend rejected the update."}
                  </InlineAlert>
                ) : null}

                <div className="flex justify-end">
                  <Button type="submit" loading={handoffMutation.isPending}>
                    <Save className="h-4 w-4" aria-hidden /> Save handoff settings
                  </Button>
                </div>
              </form>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Current handoff state" />
            <CardBody className="space-y-3 text-sm">
              <Row
                label="Warm transfer"
                value={business.warm_transfer_enabled ? "Enabled" : "Disabled"}
                ok={business.warm_transfer_enabled}
              />
              <Row
                label="Transfer number"
                value={business.warm_transfer_number || "Not set"}
                mono
              />
              <Row
                label="Default target"
                value={business.handoff_target_number || "Not set"}
                mono
              />
              <ButtonLink to="/handoffs" variant="outline" size="sm" className="w-full">
                Open handoff queue
              </ButtonLink>
            </CardBody>
          </Card>
        </div>
      ) : null}

      {tab === "integrations" ? <IntegrationsTab /> : null}

      {tab === "account" ? (
        <div className="grid gap-4 xl:grid-cols-3">
          <Card className="xl:col-span-2">
            <CardHeader
              title="API access"
              description="The browser authenticates with a workspace-scoped API key. Provider and CRM secrets never enter the bundle."
            />
            <CardBody className="space-y-4">
              <div className="rounded-lg border border-slate-200 p-3">
                <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-slate-500">
                  <KeyRound className="h-3.5 w-3.5" /> Active API key
                </p>
                <p className="mt-1 break-all font-mono text-xs text-slate-800">
                  {maskedKey}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  Stored in this browser only. Sign out clears it from localStorage.
                </p>
              </div>

              <div className="rounded-lg border border-slate-200 p-3">
                <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-slate-500">
                  <Database className="h-3.5 w-3.5" /> API base
                </p>
                <p className="mt-1 break-all font-mono text-xs text-slate-800">{API_BASE}</p>
                <p className="mt-1 text-xs text-slate-500">
                  Configured at build time with VITE_API_BASE_URL. Development uses the Vite
                  proxy to the FastAPI server.
                </p>
              </div>

              <div className="rounded-lg border border-slate-200 p-3">
                <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                  Session
                </p>
                <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
                  <div>
                    <dt className="text-slate-500">Business</dt>
                    <dd className="font-mono text-slate-800">{business.business_id}</dd>
                  </div>
                  <div>
                    <dt className="text-slate-500">Workspace</dt>
                    <dd className="text-slate-800">{business.name}</dd>
                  </div>
                  <div>
                    <dt className="text-slate-500">Provider</dt>
                    <dd className="text-slate-800">
                      {business.provider_configured ? "Configured" : "Missing"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-slate-500">Outbound</dt>
                    <dd className="text-slate-800">
                      {business.outbound_calls_enabled ? "Enabled" : "Disabled"}
                    </dd>
                  </div>
                </dl>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(business.business_id);
                      toast.success("Business id copied");
                    } catch {
                      toast.error("Clipboard unavailable");
                    }
                  }}
                >
                  <ClipboardCopy className="h-4 w-4" aria-hidden /> Copy business id
                </Button>
                <Button
                  variant="danger"
                  onClick={() => {
                    logout();
                    navigate("/login", { replace: true });
                  }}
                >
                  <LogOut className="h-4 w-4" aria-hidden /> Sign out
                </Button>
              </div>
            </CardBody>
          </Card>

          <div className="space-y-4">
            <Card>
              <CardHeader title="Team & roles" />
              <CardBody>
                <div className="flex items-start gap-2 rounded-lg border border-dashed border-slate-300 bg-slate-50 p-3 text-sm">
                  <Users className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                  <div>
                    <p className="font-medium text-slate-800">Not available in this backend</p>
                    <p className="mt-1 text-xs leading-relaxed text-slate-500">
                      There are no member, invitation or role endpoints in the API contract.
                      Access is workspace-scoped by the API key, so this page does not invent
                      team management controls.
                    </p>
                  </div>
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Notifications" />
              <CardBody>
                <div className="flex items-start gap-2 rounded-lg border border-dashed border-slate-300 bg-slate-50 p-3 text-sm">
                  <Bell className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                  <div>
                    <p className="font-medium text-slate-800">No notification preferences API</p>
                    <p className="mt-1 text-xs leading-relaxed text-slate-500">
                      The backend has no notification settings contract, so no toggles are
                      shown. Operational state appears in the handoff and job queues.
                    </p>
                  </div>
                </div>
              </CardBody>
            </Card>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Integrations                                                        */
/* ------------------------------------------------------------------ */

function IntegrationsTab() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [crmOpen, setCrmOpen] = useState(false);
  const [editingCrm, setEditingCrm] = useState<CRMConnection | null>(null);
  const [integrationOpen, setIntegrationOpen] = useState(false);

  const crmQuery = useQuery({
    queryKey: ["crm-connections"],
    queryFn: listCrmConnections,
  });

  const integrationsQuery = useQuery({
    queryKey: ["workflow-integrations"],
    queryFn: listIntegrations,
  });

  const crmConnections = crmQuery.data ?? [];
  const integrations = integrationsQuery.data?.items ?? [];

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card>
        <CardHeader
          title="CRM connection"
          description="The backend verifies the URL and API key before saving. The key is stored server-side."
          actions={
            <Button
              size="sm"
              onClick={() => {
                setEditingCrm(null);
                setCrmOpen(true);
              }}
            >
              <Plus className="h-3.5 w-3.5" /> {crmConnections.length ? "Add another" : "Connect"}
            </Button>
          }
        />
        {crmQuery.isPending ? (
          <CardBody>
            <Skeleton className="h-24 w-full" />
          </CardBody>
        ) : crmQuery.error ? (
          <ErrorState
            error={crmQuery.error}
            title="CRM connections unavailable"
            onRetry={() => void crmQuery.refetch()}
          />
        ) : crmConnections.length === 0 ? (
          <EmptyState
            icon={<Link2 className="h-5 w-5" aria-hidden />}
            title="No CRM connected"
            description="Connect a CRM to synchronize accepted leads. Only the backend holds the CRM secret."
            action={
              <Button size="sm" onClick={() => setCrmOpen(true)}>
                Connect CRM
              </Button>
            }
          />
        ) : (
          <ul className="divide-y divide-slate-100">
            {crmConnections.map((connection) => (
              <li key={connection.connection_id} className="px-5 py-4">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-medium text-slate-900">{connection.name}</p>
                  <Badge tone={connection.is_active ? "success" : "neutral"}>
                    {connection.is_active ? "active" : "inactive"}
                  </Badge>
                  {connection.has_api_key ? (
                    <Badge tone="accent">key stored</Badge>
                  ) : (
                    <Badge tone="warning">no key</Badge>
                  )}
                </div>
                <p className="mt-1 break-all font-mono text-[11px] text-slate-500">
                  {connection.crm_base_url}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  Timeout {connection.crm_timeout_seconds}s · updated{" "}
                  {formatDateTime(connection.updated_at)}
                </p>
                <Button
                  variant="ghost"
                  size="sm"
                  className="mt-2"
                  onClick={() => {
                    setEditingCrm(connection);
                    setCrmOpen(true);
                  }}
                >
                  Update connection
                </Button>
              </li>
            ))}
          </ul>
        )}
        <div className="border-t border-slate-100 px-4 py-2 text-[11px] text-slate-400">
          CRM failures surface on lead sync; this page does not claim a sync succeeded.
          Use leads and audit logs to verify.
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Workflow integrations"
          description="Registered external destinations for recovery automation. Registration does not test connectivity."
          actions={
            <Button size="sm" variant="outline" onClick={() => setIntegrationOpen(true)}>
              <Plus className="h-3.5 w-3.5" /> Register
            </Button>
          }
        />
        {integrationsQuery.isPending ? (
          <CardBody>
            <Skeleton className="h-24 w-full" />
          </CardBody>
        ) : integrationsQuery.error ? (
          <ErrorState
            error={integrationsQuery.error}
            title="Integrations unavailable"
            onRetry={() => void integrationsQuery.refetch()}
          />
        ) : integrations.length === 0 ? (
          <EmptyState
            icon={<Webhook className="h-5 w-5" aria-hidden />}
            title="No integrations registered"
            description="Register a workflow destination. The backend records the connection status and any last error; the UI does not simulate a successful sync."
            action={
              <Button size="sm" variant="outline" onClick={() => setIntegrationOpen(true)}>
                Register integration
              </Button>
            }
          />
        ) : (
          <ul className="divide-y divide-slate-100">
            {integrations.map((integration) => (
              <li key={integration.id} className="px-5 py-4">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-medium text-slate-900">{integration.name}</p>
                  <Badge tone="neutral">{integration.provider}</Badge>
                  <Badge
                    tone={
                      integration.status === "active" || integration.status === "connected"
                        ? "success"
                        : integration.status === "error"
                          ? "danger"
                          : "warning"
                    }
                  >
                    {integration.status}
                  </Badge>
                </div>
                {integration.capabilities?.length ? (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {integration.capabilities.map((capability) => (
                      <span
                        key={capability}
                        className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-600"
                      >
                        {capability}
                      </span>
                    ))}
                  </div>
                ) : null}
                <p className="mt-1 text-xs text-slate-500">
                  Last checked {formatDateTime(integration.last_checked_at)}
                </p>
                {integration.last_error ? (
                  <p className="mt-1 text-xs text-rose-600">{integration.last_error}</p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <CrmDialog
        open={crmOpen}
        onClose={() => {
          setCrmOpen(false);
          setEditingCrm(null);
        }}
        initial={editingCrm}
        onSaved={(result) => {
          queryClient.invalidateQueries({ queryKey: ["crm-connections"] });
          if (result.verified) {
            toast.success("CRM connection verified", result.message);
          } else {
            toast.info(
              "CRM saved but not verified",
              result.message ||
                "The backend could not verify the connection. Check the URL and key.",
            );
          }
          setCrmOpen(false);
          setEditingCrm(null);
        }}
      />

      <IntegrationDialog
        open={integrationOpen}
        onClose={() => setIntegrationOpen(false)}
        onCreated={() => {
          queryClient.invalidateQueries({ queryKey: ["workflow-integrations"] });
          toast.success(
            "Integration registered",
            "Connectivity is not verified by this action; check the reported status.",
          );
          setIntegrationOpen(false);
        }}
      />
    </div>
  );
}

function CrmDialog({
  open,
  onClose,
  initial,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  initial: CRMConnection | null;
  onSaved: (result: { verified: boolean; message: string }) => void;
}) {
  const [connectionId, setConnectionId] = useState("");
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [timeout, setTimeoutSeconds] = useState("30");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ verified: boolean; message: string } | null>(
    null,
  );

  const mutation = useMutation({
    mutationFn: () =>
      saveCrmConnection({
        connection_id: connectionId || undefined,
        name: name.trim(),
        crm_base_url: baseUrl.trim(),
        crm_x_api_key: apiKey,
        crm_timeout_seconds: Number(timeout) || 30,
      }),
    onSuccess: (response) => {
      setResult(response);
      onSaved(response);
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : "The CRM connection was rejected.");
    },
  });

  function reset() {
    setConnectionId("");
    setName("");
    setBaseUrl("");
    setApiKey("");
    setTimeoutSeconds("30");
    setError(null);
    setResult(null);
    mutation.reset();
  }

  useEffect(() => {
    if (!open) {
      reset();
      return;
    }
    if (initial) {
      setConnectionId(initial.connection_id);
      setName(initial.name);
      setBaseUrl(initial.crm_base_url);
      setTimeoutSeconds(String(initial.crm_timeout_seconds));
    } else {
      setConnectionId("");
      setName("");
      setBaseUrl("");
      setTimeoutSeconds("30");
    }
    setApiKey("");
    setError(null);
    setResult(null);
    mutation.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initial]);

  let urlValid = false;
  try {
    const parsed = new URL(baseUrl);
    urlValid = parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    urlValid = false;
  }

  const valid = name.trim() && urlValid && apiKey.trim();

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={connectionId ? "Update CRM connection" : "Connect a CRM"}
      description="The backend verifies the URL and API key before saving. Re-enter the key to update the connection."
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button
            onClick={() => {
              setError(null);
              mutation.mutate();
            }}
            disabled={!valid}
            loading={mutation.isPending}
          >
            <Link2 className="h-4 w-4" /> Save connection
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Connection name" htmlFor="crm-name" required>
          <Input
            id="crm-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Primary CRM"
            disabled={mutation.isPending}
          />
        </Field>
        <Field
          label="CRM base URL"
          htmlFor="crm-url"
          required
          hint="The server calls this URL. It must be reachable from the backend."
        >
          <Input
            id="crm-url"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="https://crm.example.com/api"
            disabled={mutation.isPending}
          />
        </Field>
        <Field
          label="CRM API key"
          htmlFor="crm-key"
          required
          hint="Stored server-side. It is never returned by the API."
        >
          <Input
            id="crm-key"
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder="Enter the CRM key"
            disabled={mutation.isPending}
            autoComplete="off"
          />
        </Field>
        <Field label="Timeout (seconds)" htmlFor="crm-timeout">
          <Input
            id="crm-timeout"
            type="number"
            min={1}
            max={120}
            value={timeout}
            onChange={(event) => setTimeoutSeconds(event.target.value)}
            disabled={mutation.isPending}
          />
        </Field>

        {error ? (
          <InlineAlert tone="danger" title="The connection was not saved">
            {error}
          </InlineAlert>
        ) : null}
        {result ? (
          <InlineAlert
            tone={result.verified ? "success" : "warning"}
            title={result.verified ? "Connection verified" : "Saved but not verified"}
          >
            {result.message}
          </InlineAlert>
        ) : null}

        <InlineAlert tone="info" title="Secret handling">
          This form sends the CRM key directly to the FastAPI backend over the configured API.
          The browser never stores it after the request.
        </InlineAlert>

        <button type="submit" className="sr-only" disabled={!valid}>
          Save connection
        </button>
      </div>
    </Dialog>
  );
}

function IntegrationDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [provider, setProvider] = useState("webhook");
  const [name, setName] = useState("");
  const [config, setConfig] = useState("{}");
  const [capabilities, setCapabilities] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => {
      let parsed: Record<string, unknown>;
      try {
        const value = JSON.parse(config || "{}");
        if (!value || typeof value !== "object" || Array.isArray(value)) {
          throw new Error("Config must be a JSON object.");
        }
        parsed = value as Record<string, unknown>;
      } catch (err) {
        throw new Error(
          err instanceof Error && err.message.includes("JSON object")
            ? err.message
            : "Config must be valid JSON object syntax.",
        );
      }
      return createIntegration({
        provider,
        name: name.trim(),
        config: parsed,
        capabilities: capabilities
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
      });
    },
    onSuccess: onCreated,
    onError: (err) =>
      setError(
        err instanceof Error ? err.message : "The integration was rejected.",
      ),
  });

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Register a workflow integration"
      description="The backend records the connection. Registration alone does not prove connectivity."
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button
            onClick={() => {
              setError(null);
              mutation.mutate();
            }}
            disabled={!name.trim()}
            loading={mutation.isPending}
          >
            <Plus className="h-4 w-4" /> Register
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Provider" htmlFor="integration-provider">
            <Select
              id="integration-provider"
              value={provider}
              onChange={(event) => setProvider(event.target.value)}
            >
              <option value="webhook">Webhook</option>
              <option value="slack">Slack</option>
              <option value="google_sheets">Google Sheets</option>
              <option value="crm">CRM</option>
              <option value="other">Other</option>
            </Select>
          </Field>
          <Field label="Display name" htmlFor="integration-name" required>
            <Input
              id="integration-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Ops notifications"
            />
          </Field>
        </div>
        <Field
          label="Config (JSON object)"
          htmlFor="integration-config"
          hint="Sent as-is to the backend. Do not put secrets in a config the UI will later display."
        >
          <Textarea
            id="integration-config"
            rows={5}
            value={config}
            onChange={(event) => setConfig(event.target.value)}
            className="font-mono text-xs"
          />
        </Field>
        <Field
          label="Capabilities"
          htmlFor="integration-capabilities"
          hint="Comma separated, e.g. notify, create_task."
        >
          <Input
            id="integration-capabilities"
            value={capabilities}
            onChange={(event) => setCapabilities(event.target.value)}
            placeholder="notify, create_task"
          />
        </Field>
        {error ? (
          <InlineAlert tone="danger" title="Integration was not registered">
            {error}
          </InlineAlert>
        ) : null}
        <InlineAlert tone="warning" title="No credentials are verified here">
          The backend may report a status of active or error later. Callora displays the
          backend status and last error; it never claims a test succeeded.
        </InlineAlert>
      </div>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

function workspaceDefaults(business: Business): WorkspaceForm {
  return {
    name: business.name,
    timezone: business.timezone,
    default_language: business.default_language || "en",
    supported_languages: (business.supported_languages?.length
      ? business.supported_languages
      : ["en"]) as ("en" | "hi" | "kn")[],
  };
}

function callingDefaults(business: Business): CallingForm {
  return {
    calling_window_start: business.calling_window_start,
    calling_window_end: business.calling_window_end,
    calling_days: business.calling_days,
    max_calls_per_lead_per_day: business.max_calls_per_lead_per_day,
    max_calls_per_business_per_day: business.max_calls_per_business_per_day,
    max_concurrent_calls: business.max_concurrent_calls,
    require_consent: business.require_consent,
    dnd_enabled: business.dnd_enabled,
  };
}

function handoffDefaults(business: Business): HandoffForm {
  return {
    handoff_target_number: business.handoff_target_number || "",
    warm_transfer_enabled: business.warm_transfer_enabled,
    warm_transfer_number: business.warm_transfer_number || "",
  };
}

function Row({
  label,
  value,
  mono,
  ok,
}: {
  label: string;
  value: string;
  mono?: boolean;
  ok?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-slate-100 py-1.5 last:border-0">
      <span className="shrink-0 text-slate-500">{label}</span>
      <span
        className={
          mono
            ? "break-all text-right font-mono text-[11px] text-slate-800"
            : "text-right text-slate-800"
        }
      >
        {ok !== undefined ? (
          <span className="inline-flex items-center gap-1">
            {ok ? (
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
            ) : (
              <ShieldAlert className="h-3.5 w-3.5 text-amber-500" />
            )}
            {value}
          </span>
        ) : (
          value
        )}
      </span>
    </div>
  );
}
