import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, PhoneOutgoing, ShieldAlert } from "lucide-react";

import { useBusiness } from "@/app/auth";
import { LeadPicker } from "@/components/domain/lead-picker";
import { DecisionBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ButtonLink } from "@/components/ui/button-link";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Switch } from "@/components/ui/form";
import { InlineAlert } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { LANGUAGE_NAMES } from "@/lib/constants";
import { formatDateTime } from "@/lib/utils";
import { initiateCall } from "@/services/callora";
import type { Call, CallInitiationResponse } from "@/types/api";

/**
 * Starts a real outbound call. The backend compliance gate is authoritative:
 * the dialog only reports the decision the API returns and never assumes the
 * call was placed.
 */
export function StartCallDialog({
  open,
  onClose,
  defaultLeadId,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  defaultLeadId?: string;
  onCreated?: (call: Call) => void;
}) {
  const business = useBusiness();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [leadId, setLeadId] = useState(defaultLeadId || "");
  const [language, setLanguage] = useState(business.default_language || "en");
  const [scheduledAt, setScheduledAt] = useState("");
  const [dryRun, setDryRun] = useState(false);
  const [result, setResult] = useState<CallInitiationResponse | null>(null);

  useEffect(() => {
    if (!open) return;
    setLeadId(defaultLeadId || "");
    setLanguage(business.default_language || "en");
    setScheduledAt("");
    setDryRun(false);
    setResult(null);
  }, [open, defaultLeadId, business.default_language]);

  const mutation = useMutation({
    mutationFn: () =>
      initiateCall({
        lead_id: leadId,
        language,
        scheduled_at: scheduledAt ? new Date(scheduledAt).toISOString() : null,
        dry_run: dryRun,
      }),
    onSuccess: (response) => {
      setResult(response);
      void queryClient.invalidateQueries({ queryKey: ["calls"] });
      void queryClient.invalidateQueries({ queryKey: ["voice-stats"] });
      void queryClient.invalidateQueries({ queryKey: ["overview-calls"] });
      void queryClient.invalidateQueries({ queryKey: ["funnel"] });
      void queryClient.invalidateQueries({ queryKey: ["compliance"] });
      if (response.simulated) {
        toast.info(
          "Simulated call recorded",
          "No telephone call was placed. The backend stored the simulated attempt.",
        );
      } else {
        toast.success(
          "Call requested",
          response.message || "The provider accepted the request.",
        );
      }
      onCreated?.(response.call);
    },
    onError: (error) => {
      toast.error(
        "The call was not started",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const languages = business.supported_languages?.length
    ? business.supported_languages
    : [business.default_language || "en"];

  return (
    <Dialog
      open={open}
      onClose={() => {
        if (mutation.isPending) return;
        onClose();
      }}
      title={result ? "Call request result" : "Start an outbound call"}
      description={
        result
          ? "The backend decision and the stored call record are shown below."
          : "The compliance gate evaluates consent, DND, calling hours, quotas and the agent status before anything is dialled."
      }
      size="lg"
      footer={
        result ? (
          <>
            <Button variant="outline" onClick={onClose}>
              Close
            </Button>
            <ButtonLink to={`/calls/${result.call.id}`}>
              Open call details
            </ButtonLink>
          </>
        ) : (
          <>
            <Button
              variant="outline"
              onClick={onClose}
              disabled={mutation.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={() => mutation.mutate()}
              disabled={!leadId}
              loading={mutation.isPending}
            >
              <PhoneOutgoing className="h-4 w-4" aria-hidden />
              {dryRun ? "Run dry run" : "Start call"}
            </Button>
          </>
        )
      }
    >
      {result ? (
        <div className="space-y-4">
          <InlineAlert
            tone={result.compliance_decision === "blocked" ? "danger" : "success"}
            title={
              result.compliance_decision === "blocked"
                ? "The compliance gate blocked this call"
                : result.simulated
                  ? "Simulated attempt recorded"
                  : "Call request accepted"
            }
          >
            {result.message ||
              "The backend accepted the request. Status updates arrive through provider webhooks."}
          </InlineAlert>

          <dl className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg border border-slate-200 p-3">
              <dt className="text-xs text-slate-500">Call id</dt>
              <dd className="mt-0.5 font-mono text-xs text-slate-800">
                {result.call.id}
              </dd>
            </div>
            <div className="rounded-lg border border-slate-200 p-3">
              <dt className="text-xs text-slate-500">Compliance decision</dt>
              <dd className="mt-1 flex items-center gap-2">
                <DecisionBadge decision={result.compliance_decision} />
                <span className="font-mono text-[11px] text-slate-400">
                  {result.compliance_decision_id}
                </span>
              </dd>
            </div>
            <div className="rounded-lg border border-slate-200 p-3">
              <dt className="text-xs text-slate-500">Call status</dt>
              <dd className="mt-0.5 text-sm font-medium capitalize text-slate-800">
                {result.call.status.replace(/_/g, " ")}
              </dd>
            </div>
            <div className="rounded-lg border border-slate-200 p-3">
              <dt className="text-xs text-slate-500">
                {result.simulated ? "Simulated" : "Scheduled"}
              </dt>
              <dd className="mt-0.5 text-sm text-slate-800">
                {result.simulated
                  ? "Yes — no telephone call was placed."
                  : formatDateTime(result.call.scheduled_at || result.call.created_at)}
              </dd>
            </div>
          </dl>

          {result.compliance_checks?.length ? (
            <div className="rounded-lg border border-slate-200">
              <p className="border-b border-slate-100 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Checks evaluated by the backend
              </p>
              <ul className="divide-y divide-slate-100">
                {result.compliance_checks.map((check, index) => (
                  <li
                    key={String(check.code || check.name || index)}
                    className="flex items-start justify-between gap-3 px-3 py-2 text-xs"
                  >
                    <span className="font-mono text-slate-600">
                      {String(check.code || check.name || `check_${index + 1}`)}
                    </span>
                    <span className="text-right text-slate-500">
                      {String(check.message || check.result || "")}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="space-y-4">
          <LeadPicker
            value={leadId}
            onChange={(lead) => setLeadId(lead?.id || "")}
            disabled={mutation.isPending}
          />

          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label="Language"
              htmlFor="call-language"
              hint="The agent may still switch language mid-call."
            >
              <Select
                id="call-language"
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
                disabled={mutation.isPending}
              >
                {languages.map((code) => (
                  <option key={code} value={code}>
                    {LANGUAGE_NAMES[code] || code}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              label="Schedule for later"
              htmlFor="call-scheduled-at"
              hint="Optional. Leave blank to call immediately."
            >
              <Input
                id="call-scheduled-at"
                type="datetime-local"
                value={scheduledAt}
                onChange={(event) => setScheduledAt(event.target.value)}
                disabled={mutation.isPending}
              />
            </Field>
          </div>

          <Switch
            checked={dryRun}
            onChange={setDryRun}
            label="Dry run"
            description="Ask the backend to record a simulated attempt without dialling the lead."
          />

          <InlineAlert tone="warning" title="Before you dial">
            Calls are only placed when the agent is active, the provider is configured,
            consent evidence is valid, the number is not on the DND list, and the current
            time is inside the calling window. If any check fails, the backend blocks the
            call and explains why.
          </InlineAlert>

          {!business.provider_configured ? (
            <InlineAlert tone="danger" title="Voice provider not configured">
              This deployment has no provider credentials. A dry run may still be recorded,
              but a live call cannot be placed until the server is configured.
            </InlineAlert>
          ) : null}

          {mutation.error ? (
            <InlineAlert tone="danger" title="Call rejected">
              {mutation.error instanceof Error
                ? mutation.error.message
                : "The backend rejected the call request."}
            </InlineAlert>
          ) : null}

          <div className="flex items-start gap-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            The final decision is written to the compliance audit log. Callora displays the
            backend's result rather than predicting it.
          </div>

          {dryRun ? (
            <p className="flex items-center gap-1.5 text-xs text-slate-500">
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" aria-hidden />
              Dry run still evaluates the compliance gate and stores the attempt.
            </p>
          ) : null}
        </div>
      )}
    </Dialog>
  );
}
