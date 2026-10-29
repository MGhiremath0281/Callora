import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { zodResolver } from "@hookform/resolvers/zod";
import { useFieldArray, useForm } from "react-hook-form";
import { z } from "zod";
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  CheckCircle2,
  ClipboardCopy,
  ExternalLink,
  Info,
  MessageSquareText,
  Mic,
  Play,
  Plus,
  Save,
  SlidersHorizontal,
  Sparkles,
  Square,
  Trash2,
  Volume2,
  Wrench,
} from "lucide-react";

import { useAuth, useBusiness } from "@/app/auth";
import { JsonBlock } from "@/components/domain/json-block";
import { KnowledgeManager } from "@/components/domain/knowledge-manager";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ButtonLink } from "@/components/ui/button-link";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Input, Select, Switch, Textarea } from "@/components/ui/form";
import { PageHeader } from "@/components/ui/page-header";
import {
  InlineAlert,
  Skeleton,
  UnavailableNotice,
} from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import {
  CALLING_DAYS,
  LANGUAGE_NAMES,
  SUPPORTED_LANGUAGES,
  TIMEZONES,
} from "@/lib/constants";
import { cn } from "@/lib/utils";
import { getCustomFunctions, initiateCall, listLeads, updateBusiness } from "@/services/callora";
import type { Business } from "@/types/api";

const phonePattern = /^\+?[0-9][0-9\s-]{5,20}$/;

export const VOICE_CATALOG = [
  {
    id: "priya_en",
    name: "Priya",
    language: "Indian English",
    role: "B2B Sales Representative",
    style: "Warm, Consultative, Articulate",
    sampleText: "Namaste! This is Priya from Callora. I am calling to see if you would be interested in scaling your outbound sales qualification.",
  },
  {
    id: "aarav_hi",
    name: "Aarav",
    language: "Hindi",
    role: "Outbound Qualification Specialist",
    style: "Natural, Persuasive, Professional",
    sampleText: "नमस्ते! मैं कॉलरा से आरव बात कर रहा हूँ। क्या आप अपने बिज़नेस के लिए ऑटोमेटेड आउटबाउंड कॉलिंग एक्सप्लोर करना चाहेंगे?",
  },
  {
    id: "kavya_kn",
    name: "Kavya",
    language: "Kannada",
    role: "Regional Outreach Representative",
    style: "Friendly, Fluent, Courteous",
    sampleText: "ನಮಸ್ಕಾರ! ನಾನು ಕಾಲೋರಾದಿಂದ ಕಾವ್ಯ ಮಾತನಾಡುತ್ತಿದ್ದೇನೆ. ನಿಮ್ಮ ವ್ಯಾಪಾರದ ಮಾರಾಟ ತಂಡಕ್ಕೆ ನಮ್ಮ ಧ್ವನಿ ಏಜೆಂಟ್ ಹೇಗೆ ಸಹಾಯ ಮಾಡಬಹುದು ಎಂದು ಚರ್ಚಿಸೋಣವೇ?",
  },
  {
    id: "vikram_en",
    name: "Vikram",
    language: "Indian English",
    role: "Enterprise Account Executive",
    style: "Authoritative, Structured, Direct",
    sampleText: "Good afternoon. Vikram here from Callora. Reaching out to senior leadership regarding automated outbound sales qualification.",
  },
  {
    id: "ananya_mix",
    name: "Ananya",
    language: "Hinglish (Hindi + English)",
    role: "Bilingual Growth Executive",
    style: "Dynamic, Modern, Conversational",
    sampleText: "Hi there! Main Ananya from Callora. We help Indian companies qualify leads 5x faster with multilingual voice AI.",
  },
];

function WaveformVisualizer({ isPlaying }: { isPlaying: boolean }) {
  return (
    <div className="flex items-center gap-1 h-6 px-1.5" aria-hidden="true">
      {[10, 22, 14, 26, 18, 12, 24, 16].map((h, i) => (
        <span
          key={i}
          className={`w-1 rounded-full transition-all duration-200 ${
            isPlaying ? "bg-accent-600 animate-pulse" : "bg-slate-300"
          }`}
          style={{
            height: isPlaying ? `${Math.max(6, h * (1 + (i % 3) * 0.25))}px` : "6px",
            animationDelay: `${i * 100}ms`,
          }}
        />
      ))}
    </div>
  );
}

const agentSchema = z
  .object({
    name: z.string().trim().min(1, "Agent name is required.").max(255),
    sales_objective: z.string().max(500).optional().default(""),
    business_offering: z.string().max(1000).optional().default(""),
    disclosure_message: z.string().max(500).optional().default(""),
    voice_id: z.string().optional().default("priya_en"),
    speaking_speed: z.coerce.number().min(0.5).max(2.0).optional().default(1.0),
    agent_instructions: z.string().max(20000).optional().default(""),
    supported_languages: z
      .array(z.enum(["en", "hi", "kn"]))
      .min(1, "Select at least one language."),
    default_language: z.string().min(1),
    bolna_agent_id: z.string().max(128).optional().default(""),
    calling_window_start: z.coerce.number().int().min(0).max(23),
    calling_window_end: z.coerce.number().int().min(0).max(23),
    calling_days: z.array(z.number().int().min(0).max(6)).min(1, "Select at least one day."),
    max_calls_per_lead_per_day: z.coerce.number().int().min(1).max(50),
    max_calls_per_business_per_day: z.coerce.number().int().min(1).max(100000),
    max_concurrent_calls: z.coerce.number().int().min(1).max(1000),
    require_consent: z.boolean(),
    dnd_enabled: z.boolean(),
    warm_transfer_enabled: z.boolean(),
    warm_transfer_number: z.string().max(32).optional().default(""),
    handoff_target_number: z.string().max(32).optional().default(""),
    questions: z
      .array(
        z.object({
          key: z.string().max(64).optional().default(""),
          question: z.string().max(500).optional().default(""),
        }),
      )
      .default([]),
  })
  .superRefine((values, ctx) => {
    if (!values.supported_languages.includes(values.default_language as "en" | "hi" | "kn")) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["default_language"],
        message: "The primary language must also be a supported language.",
      });
    }
    if (values.calling_window_start === values.calling_window_end) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["calling_window_end"],
        message: "The window must end at a different hour than it starts.",
      });
    }
    if (
      values.warm_transfer_number &&
      !phonePattern.test(values.warm_transfer_number.trim())
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["warm_transfer_number"],
        message: "Enter a valid phone number with country code.",
      });
    }
    if (
      values.handoff_target_number &&
      !phonePattern.test(values.handoff_target_number.trim())
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["handoff_target_number"],
        message: "Enter a valid phone number with country code.",
      });
    }
    const keys = new Set<string>();
    values.questions.forEach((question, index) => {
      if (question.question && !question.key.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["questions", index, "key"],
          message: "Give the answer a stable key so it can be stored.",
        });
      }
      if (question.key && keys.has(question.key)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["questions", index, "key"],
          message: "Question keys must be unique.",
        });
      }
      if (question.key) keys.add(question.key);
    });
  });

type AgentForm = z.infer<typeof agentSchema>;

function toFormValues(business: Business): AgentForm {
  return {
    name: business.name,
    sales_objective: "Qualify outbound leads, confirm purchase intent, and arrange warm transfer to human sales rep",
    business_offering: business.name,
    disclosure_message: `Hello, this is an automated outbound call on behalf of ${business.name}. I am an AI sales assistant.`,
    voice_id: "priya_en",
    speaking_speed: 1.0,
    agent_instructions: business.agent_instructions || "",
    supported_languages: (business.supported_languages?.filter((code) =>
      SUPPORTED_LANGUAGES.includes(code as "en" | "hi" | "kn"),
    ) || []) as Array<"en" | "hi" | "kn">,
    default_language: business.default_language || "en",
    bolna_agent_id: business.bolna_agent_id || "",
    calling_window_start: business.calling_window_start ?? 9,
    calling_window_end: business.calling_window_end ?? 20,
    calling_days: business.calling_days?.length ? business.calling_days : [0, 1, 2, 3, 4],
    max_calls_per_lead_per_day: business.max_calls_per_lead_per_day ?? 3,
    max_calls_per_business_per_day: business.max_calls_per_business_per_day ?? 500,
    max_concurrent_calls: business.max_concurrent_calls ?? 5,
    require_consent: business.require_consent,
    dnd_enabled: business.dnd_enabled,
    warm_transfer_enabled: business.warm_transfer_enabled,
    warm_transfer_number: business.warm_transfer_number || "",
    handoff_target_number: business.handoff_target_number || "",
    questions: (business.qualification_questions || []).map((item) => ({
      key: String(item.key || item.id || ""),
      question: String(item.question || item.text || ""),
    })),
  };
}

function getInitialFormValues(business: Business, agentId?: string): AgentForm {
  const base = toFormValues(business);
  if (agentId && agentId.startsWith("agt_")) {
    try {
      const raw = localStorage.getItem(`callora_custom_agents_${business.business_id}`);
      if (raw) {
        const list = JSON.parse(raw);
        const match = list.find((a: any) => a.id === agentId);
        if (match) {
          return {
            ...base,
            name: match.name || base.name,
            agent_instructions: match.description || base.agent_instructions,
            supported_languages: match.languages?.length ? match.languages : base.supported_languages,
          };
        }
      }
    } catch (e) {
      console.error(e);
    }
  }
  return base;
}

export default function AgentStudioPage() {
  const { agentId } = useParams();
  const business = useBusiness();
  const { refreshBusiness } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") || "config";
  const [activationOpen, setActivationOpen] = useState(false);
  const [testModalOpen, setTestModalOpen] = useState(false);
  const [isPlayingVoice, setIsPlayingVoice] = useState(false);
  const [playingVoiceId, setPlayingVoiceId] = useState<string | null>(null);

  const form = useForm<AgentForm>({
    resolver: zodResolver(agentSchema),
    defaultValues: getInitialFormValues(business, agentId),
    mode: "onBlur",
  });

  const playVoiceSample = (voiceId: string) => {
    const voice = VOICE_CATALOG.find((v) => v.id === voiceId) || VOICE_CATALOG[0];
    if (isPlayingVoice && playingVoiceId === voiceId) {
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }
      setIsPlayingVoice(false);
      setPlayingVoiceId(null);
      return;
    }

    setIsPlayingVoice(true);
    setPlayingVoiceId(voiceId);

    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(voice.sampleText);
      utterance.rate = form.getValues("speaking_speed") || 1.0;
      utterance.onend = () => {
        setIsPlayingVoice(false);
        setPlayingVoiceId(null);
      };
      utterance.onerror = () => {
        setIsPlayingVoice(false);
        setPlayingVoiceId(null);
      };
      window.speechSynthesis.speak(utterance);
    } else {
      setTimeout(() => {
        setIsPlayingVoice(false);
        setPlayingVoiceId(null);
      }, 3000);
    }
  };

  const {
    fields: questionFields,
    append: appendQuestion,
    remove: removeQuestion,
  } = useFieldArray({ control: form.control, name: "questions" });

  useEffect(() => {
    form.reset(getInitialFormValues(business, agentId));
  }, [business, agentId, form]);

  useEffect(() => {
    if (!form.formState.isDirty) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [form.formState.isDirty]);

  const toolsQuery = useQuery({
    queryKey: ["voice-tools"],
    queryFn: getCustomFunctions,
    enabled: tab === "advanced",
  });

  const saveMutation = useMutation({
    mutationFn: async (values: AgentForm) => {
      if (agentId && agentId.startsWith("agt_")) {
        const storageKey = `callora_custom_agents_${business.business_id}`;
        let existing: any[] = [];
        try {
          const raw = localStorage.getItem(storageKey);
          if (raw) existing = JSON.parse(raw);
        } catch (e) {
          console.error(e);
        }
        const updatedList = existing.map((a) =>
          a.id === agentId
            ? {
                ...a,
                name: values.name.trim(),
                description: values.agent_instructions?.slice(0, 240) || values.sales_objective || a.description,
                languages: values.supported_languages,
                updated: new Date().toISOString(),
                voice: VOICE_CATALOG.find((v) => v.id === values.voice_id)?.name || "Priya (Indian English)",
              }
            : a,
        );
        localStorage.setItem(storageKey, JSON.stringify(updatedList));
        return { name: values.name };
      }

      return updateBusiness({
        name: values.name.trim(),
        agent_instructions: values.agent_instructions?.trim() || null,
        supported_languages: values.supported_languages,
        default_language: values.default_language,
        bolna_agent_id: values.bolna_agent_id?.trim() || null,
        calling_window_start: values.calling_window_start,
        calling_window_end: values.calling_window_end,
        calling_days: values.calling_days,
        max_calls_per_lead_per_day: values.max_calls_per_lead_per_day,
        max_calls_per_business_per_day: values.max_calls_per_business_per_day,
        max_concurrent_calls: values.max_concurrent_calls,
        require_consent: values.require_consent,
        dnd_enabled: values.dnd_enabled,
        warm_transfer_enabled: values.warm_transfer_enabled,
        warm_transfer_number: values.warm_transfer_number?.trim() || null,
        handoff_target_number: values.handoff_target_number?.trim() || null,
        qualification_questions: values.questions
          .filter((question) => question.question?.trim())
          .map((question) => ({
            key: question.key?.trim(),
            question: question.question.trim(),
          })),
      });
    },
    onSuccess: async () => {
      const updated = await refreshBusiness();
      if (updated && (!agentId || agentId === business.business_id || agentId === "primary")) {
        form.reset(toFormValues(updated));
      }
      void queryClient.invalidateQueries({ queryKey: ["business"] });
      toast.success(
        "Draft saved",
        "The configuration was stored. This does not activate outbound calling.",
      );
    },
    onError: (error) => {
      toast.error(
        "Configuration was not saved",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const activationMutation = useMutation({
    mutationFn: (active: boolean) => updateBusiness({ is_active: active }),
    onSuccess: async (updated) => {
      await refreshBusiness();
      setActivationOpen(false);
      toast.success(
        updated.is_active ? "Agent activated" : "Agent deactivated",
        updated.is_active
          ? "Activation saved. Outbound calls still require deployment-level enablement and pass the compliance gate."
          : "New outbound calls will be refused until reactivated.",
      );
    },
    onError: (error) => {
      toast.error(
        "Agent status was not changed",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const tools = useMemo(() => {
    const raw = toolsQuery.data?.functions;
    return Array.isArray(raw) ? (raw as Array<Record<string, unknown>>) : [];
  }, [toolsQuery.data]);

  const supported = form.watch("supported_languages");
  const callingDays = form.watch("calling_days");
  const warmTransferEnabled = form.watch("warm_transfer_enabled");
  const errors = form.formState.errors;

  const isWorkspaceAgent =
    !agentId ||
    agentId === business.business_id ||
    agentId === "primary" ||
    agentId.startsWith("agt_");

  if (!isWorkspaceAgent) {
    return (
      <div>
        <PageHeader title="Agent not found" description="This agent does not belong to the authenticated workspace." />
        <UnavailableNotice
          title="Agent configuration is business-scoped"
          description="The requested agent id does not match the business resolved by the API key. Callora will not load another tenant's configuration."
          dependencies={["GET /api/v1/voice/business", "Authenticated business context"]}
        />
        <div className="mt-4">
          <ButtonLink to="/agents" variant="outline">
            <ArrowLeft className="h-4 w-4" /> Back to agents
          </ButtonLink>
        </div>
      </div>
    );
  }

  function toggleLanguage(code: "en" | "hi" | "kn") {
    const current = form.getValues("supported_languages");
    const next = current.includes(code)
      ? current.filter((value) => value !== code)
      : [...current, code];
    form.setValue("supported_languages", next, { shouldDirty: true, shouldValidate: true });
    if (!next.includes(form.getValues("default_language") as "en" | "hi" | "kn") && next[0]) {
      form.setValue("default_language", next[0], { shouldDirty: true });
    }
  }

  function toggleDay(value: number) {
    const current = form.getValues("calling_days");
    const next = current.includes(value)
      ? current.filter((day) => day !== value)
      : [...current, value].sort((a, b) => a - b);
    form.setValue("calling_days", next, { shouldDirty: true, shouldValidate: true });
  }

  function changeTab(next: string) {
    const params = new URLSearchParams(searchParams);
    if (next === "config") params.delete("tab");
    else params.set("tab", next);
    setSearchParams(params);
  }

  async function copyTools() {
    try {
      await navigator.clipboard.writeText(JSON.stringify(toolsQuery.data, null, 2));
      toast.success("Tool definition copied", "Paste it into the Bolna agent configuration.");
    } catch {
      toast.error("Clipboard unavailable", "Select the JSON manually instead.");
    }
  }

  const hasErrors = Object.keys(errors).length > 0;

  return (
    <div>
      <PageHeader
        title="Agent Studio"
        description="Configure the live sales agent. Saving a draft never activates outbound calling; activation is a separate, explicit step."
        actions={
          <>
            <ButtonLink to="/agents" variant="outline">
              <ArrowLeft className="h-4 w-4" aria-hidden /> All agents
            </ButtonLink>
            <Button
              variant="secondary"
              onClick={() => setActivationOpen(true)}
              disabled={form.formState.isDirty}
              title={
                form.formState.isDirty
                  ? "Save the draft before changing activation status."
                  : undefined
              }
            >
              {business.is_active ? "Deactivate" : "Activate"}
            </Button>
          </>
        }
        meta={
          <>
            <Badge tone={business.is_active ? "success" : "neutral"}>
              {business.is_active ? "Active" : "Draft"}
            </Badge>
            <Badge tone={form.formState.isDirty ? "warning" : "success"}>
              {form.formState.isDirty ? "Unsaved changes" : "Saved"}
            </Badge>
            <span className="text-xs text-slate-400">
              Last saved {business.updated_at ? new Date(business.updated_at).toLocaleString("en-IN") : "—"}
            </span>
          </>
        }
      />

      <div className="mb-4 flex flex-wrap gap-1 rounded-lg border border-slate-200 bg-white p-1">
        {[
          { id: "config", label: "Configuration", icon: SlidersHorizontal },
          { id: "knowledge", label: "Knowledge base", icon: BookOpen },
          { id: "advanced", label: "Advanced & tools", icon: Wrench },
        ].map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => changeTab(item.id)}
            aria-pressed={tab === item.id}
            className={cn(
              "inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition",
              tab === item.id
                ? "bg-navy-900 text-white"
                : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
            )}
          >
            <item.icon className="h-3.5 w-3.5" aria-hidden />
            {item.label}
          </button>
        ))}
      </div>

      {tab === "knowledge" ? (
        <KnowledgeManager />
      ) : tab === "advanced" ? (
        <AdvancedPanel
          tools={tools}
          toolsQuery={toolsQuery}
          onCopy={() => void copyTools()}
        />
      ) : (
        <form
          onSubmit={form.handleSubmit((values) => saveMutation.mutate(values))}
          noValidate
          className="space-y-4 pb-24"
        >
          {hasErrors ? (
            <InlineAlert tone="danger" title="Fix the highlighted fields">
              The configuration was not sent. Validation must pass first.
            </InlineAlert>
          ) : null}

          <Card>
            <CardHeader
              title="A · Basic information"
              description="The identity and core sales objective used in this workspace."
              actions={<Badge tone="neutral">Business profile</Badge>}
            />
            <CardBody className="grid gap-4 sm:grid-cols-2">
              <Field
                label="Agent name"
                htmlFor="agent-name"
                required
                error={errors.name?.message}
              >
                <Input id="agent-name" {...form.register("name")} />
              </Field>
              <Field
                label="Sales objective"
                htmlFor="agent-objective"
                hint="Target conversion outcome for this agent"
                error={errors.sales_objective?.message}
              >
                <Input
                  id="agent-objective"
                  placeholder="e.g. Qualify outbound leads and arrange warm transfer"
                  {...form.register("sales_objective")}
                />
              </Field>
              <div className="sm:col-span-2">
                <Field
                  label="Business or product offering"
                  htmlFor="agent-offering"
                  hint="Product or service details presented during outbound calls"
                  error={errors.business_offering?.message}
                >
                  <Input
                    id="agent-offering"
                    placeholder="e.g. Callora Outbound Voice Sales Platform for Indian Businesses"
                    {...form.register("business_offering")}
                  />
                </Field>
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="B · Language and voice"
              description="Hindi, Kannada and English supported. ElevenLabs-style voice catalog with preview & pacing controls."
            />
            <CardBody className="space-y-6">
              <div>
                <p className="label">Supported languages</p>
                <p className="text-xs text-slate-500 mb-2">Agent can converse in and dynamically switch between these languages during calls.</p>
                <div className="grid gap-2 sm:grid-cols-3">
                  {SUPPORTED_LANGUAGES.map((code) => (
                    <Checkbox
                      key={code}
                      label={LANGUAGE_NAMES[code]}
                      description={code.toUpperCase()}
                      checked={supported.includes(code)}
                      onChange={() => toggleLanguage(code)}
                    />
                  ))}
                </div>
                {errors.supported_languages?.message ? (
                  <p className="mt-1 text-xs font-medium text-rose-600">
                    {errors.supported_languages.message}
                  </p>
                ) : null}
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <Field
                  label="Primary language"
                  htmlFor="agent-default-language"
                  error={errors.default_language?.message}
                  required
                >
                  <Select id="agent-default-language" {...form.register("default_language")}>
                    {supported.map((code) => (
                      <option key={code} value={code}>
                        {LANGUAGE_NAMES[code]}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field
                  label="Timezone"
                  htmlFor="agent-timezone"
                  hint="Timezone changes are made in Settings → Workspace."
                >
                  <Input id="agent-timezone" value={business.timezone} readOnly disabled />
                </Field>
              </div>

              <div>
                <div className="mb-2 flex items-center justify-between">
                  <div>
                    <p className="label">Voice selection & Audio previews</p>
                    <p className="text-xs text-slate-500">
                      Select an accent-tailored conversational voice model for Indian enterprise sales.
                    </p>
                  </div>
                  <Badge tone="accent">ElevenLabs Voice Reference</Badge>
                </div>

                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {VOICE_CATALOG.map((voice) => {
                    const isSelected = form.watch("voice_id") === voice.id;
                    const isPlaying = isPlayingVoice && playingVoiceId === voice.id;
                    return (
                      <div
                        key={voice.id}
                        onClick={() => form.setValue("voice_id", voice.id, { shouldDirty: true })}
                        className={cn(
                          "relative flex flex-col justify-between rounded-xl border p-3.5 text-left transition-all cursor-pointer",
                          isSelected
                            ? "border-accent-600 bg-accent-50/60 ring-2 ring-accent-500/20 shadow-sm"
                            : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50/50",
                        )}
                      >
                        <div>
                          <div className="flex items-start justify-between gap-2">
                            <div>
                              <div className="flex items-center gap-2">
                                <span className="font-semibold text-sm text-slate-900">{voice.name}</span>
                                <Badge tone={isSelected ? "accent" : "neutral"}>
                                  {voice.language}
                                </Badge>
                              </div>
                              <p className="text-xs text-slate-500 mt-0.5">{voice.role}</p>
                            </div>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                playVoiceSample(voice.id);
                              }}
                              aria-label={isPlaying ? `Stop ${voice.name} preview` : `Play ${voice.name} preview`}
                              className={cn(
                                "flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition shadow-sm",
                                isPlaying
                                  ? "bg-accent-600 text-white animate-pulse"
                                  : "bg-slate-100 text-slate-700 hover:bg-accent-100 hover:text-accent-700",
                              )}
                            >
                              {isPlaying ? (
                                <Square className="h-3.5 w-3.5 fill-current" />
                              ) : (
                                <Play className="h-3.5 w-3.5 fill-current ml-0.5" />
                              )}
                            </button>
                          </div>
                        </div>

                        <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-2 text-xs text-slate-500">
                          <span className="truncate italic">{voice.style}</span>
                          <WaveformVisualizer isPlaying={isPlaying} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-slate-800">Speaking speed / pacing</p>
                    <p className="text-xs text-slate-500">
                      Calibrate conversational delivery rate for regional clarity and natural cadence.
                    </p>
                  </div>
                  <span className="font-mono text-xs font-semibold text-accent-700 bg-accent-100 px-2.5 py-1 rounded-full">
                    {Number(form.watch("speaking_speed") || 1.0).toFixed(2)}x
                  </span>
                </div>
                <div className="mt-3 flex items-center gap-3">
                  <span className="text-xs text-slate-400">0.75x (Deliberate)</span>
                  <input
                    type="range"
                    min="0.75"
                    max="1.5"
                    step="0.05"
                    className="h-2 w-full cursor-pointer appearance-none rounded-lg bg-slate-200 accent-accent-600"
                    {...form.register("speaking_speed", { valueAsNumber: true })}
                  />
                  <span className="text-xs text-slate-400">1.50x (Fast)</span>
                </div>
              </div>

              <Field
                label="Bolna agent id"
                htmlFor="agent-bolna-id"
                hint="The provider-side agent identifier that executes real-time telephony and tool binding."
                error={errors.bolna_agent_id?.message}
              >
                <Input
                  id="agent-bolna-id"
                  className="font-mono text-xs"
                  placeholder="e.g. 4f11f427-469e-471d-9548-12610d645d65"
                  {...form.register("bolna_agent_id")}
                />
              </Field>

              <InlineAlert tone="info" title="Mid-Call Multilingual Switching">
                Callora agents support seamless mid-call switching between Hindi, Kannada, and English.
                When a customer responds in their preferred language, the agent automatically adapts its
                speech response while retaining all qualification context.
              </InlineAlert>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="C · Conversation instructions"
              description="The canonical instruction block passed to the provider, plus the qualification questions the agent must collect."
            />
            <CardBody className="space-y-5">
              <Field
                label="Agent instructions"
                htmlFor="agent-instructions"
                hint="Describe the product, sales objective, tone, objection handling, closing behaviour and when to transfer. Never include secrets."
                error={errors.agent_instructions?.message}
              >
                <Textarea
                  id="agent-instructions"
                  rows={10}
                  placeholder={`You are a sales representative for ${business.name}.\n\nObjective: qualify the lead and book a demo.\n\nAlways disclose that you are an AI assistant.`}
                  {...form.register("agent_instructions")}
                />
              </Field>

              <InlineAlert tone="info" title="Greeting and AI disclosure">
                The backend stores one instruction block and injects the business name into
                the call. A separate greeting field or disclosure flag is not exposed, so
                write the disclosure explicitly in the instructions above.
              </InlineAlert>

              <div>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-slate-800">
                      Qualification questions
                    </p>
                    <p className="text-xs text-slate-500">
                      Stored as key + question. Answers are captured by the voice tools and
                      post-call workflow.
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => appendQuestion({ key: "", question: "" })}
                  >
                    <Plus className="h-3.5 w-3.5" aria-hidden /> Add question
                  </Button>
                </div>

                {questionFields.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-500">
                    No qualification questions. The agent will not run a structured
                    qualification flow.
                  </div>
                ) : (
                  <ul className="space-y-2">
                    {questionFields.map((field, index) => (
                      <li
                        key={field.id}
                        className="grid gap-2 rounded-lg border border-slate-200 p-3 sm:grid-cols-[160px_1fr_auto]"
                      >
                        <Field
                          label="Key"
                          htmlFor={`question-key-${index}`}
                          error={errors.questions?.[index]?.key?.message}
                        >
                          <Input
                            id={`question-key-${index}`}
                            className="font-mono text-xs"
                            placeholder="budget"
                            {...form.register(`questions.${index}.key`)}
                          />
                        </Field>
                        <Field
                          label="Question"
                          htmlFor={`question-text-${index}`}
                          error={errors.questions?.[index]?.question?.message}
                        >
                          <Input
                            id={`question-text-${index}`}
                            placeholder="What budget have you allocated for this?"
                            {...form.register(`questions.${index}.question`)}
                          />
                        </Field>
                        <div className="flex items-end">
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            aria-label="Remove question"
                            onClick={() => removeQuestion(index)}
                          >
                            <Trash2 className="h-3.5 w-3.5 text-rose-500" />
                          </Button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="D · Call behaviour"
              description="Calling windows, quotas, consent checks and human-transfer rules enforced by the backend."
            />
            <CardBody className="space-y-5">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field
                  label="Calling window starts (24h)"
                  htmlFor="window-start"
                  error={errors.calling_window_start?.message}
                >
                  <Select id="window-start" {...form.register("calling_window_start")}>
                    {Array.from({ length: 24 }).map((_, hour) => (
                      <option key={hour} value={hour}>
                        {String(hour).padStart(2, "0")}:00
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field
                  label="Calling window ends (24h)"
                  htmlFor="window-end"
                  error={errors.calling_window_end?.message}
                >
                  <Select id="window-end" {...form.register("calling_window_end")}>
                    {Array.from({ length: 24 }).map((_, hour) => (
                      <option key={hour} value={hour}>
                        {String(hour).padStart(2, "0")}:00
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>

              <div>
                <p className="label">Calling days</p>
                <div className="flex flex-wrap gap-1.5">
                  {CALLING_DAYS.map((day) => {
                    const selected = callingDays.includes(day.value);
                    return (
                      <button
                        key={day.value}
                        type="button"
                        aria-pressed={selected}
                        onClick={() => toggleDay(day.value)}
                        className={cn(
                          "rounded-lg border px-3 py-1.5 text-xs font-medium transition",
                          selected
                            ? "border-accent-300 bg-accent-50 text-accent-700"
                            : "border-slate-300 bg-white text-slate-500 hover:border-slate-400",
                        )}
                      >
                        {day.label.slice(0, 3)}
                      </button>
                    );
                  })}
                </div>
                {errors.calling_days?.message ? (
                  <p className="mt-1 text-xs font-medium text-rose-600">
                    {errors.calling_days.message}
                  </p>
                ) : null}
              </div>

              <div className="grid gap-4 sm:grid-cols-3">
                <Field
                  label="Calls per lead / day"
                  htmlFor="quota-lead"
                  error={errors.max_calls_per_lead_per_day?.message}
                >
                  <Input
                    id="quota-lead"
                    type="number"
                    min={1}
                    max={50}
                    {...form.register("max_calls_per_lead_per_day")}
                  />
                </Field>
                <Field
                  label="Calls per business / day"
                  htmlFor="quota-business"
                  error={errors.max_calls_per_business_per_day?.message}
                >
                  <Input
                    id="quota-business"
                    type="number"
                    min={1}
                    {...form.register("max_calls_per_business_per_day")}
                  />
                </Field>
                <Field
                  label="Max concurrent calls"
                  htmlFor="quota-concurrent"
                  error={errors.max_concurrent_calls?.message}
                >
                  <Input
                    id="quota-concurrent"
                    type="number"
                    min={1}
                    {...form.register("max_concurrent_calls")}
                  />
                </Field>
              </div>

              <div className="grid gap-2 sm:grid-cols-2">
                <Switch
                  checked={form.watch("require_consent")}
                  onChange={(value) =>
                    form.setValue("require_consent", value, { shouldDirty: true })
                  }
                  label="Require consent"
                  description="The compliance gate refuses calls without valid consent evidence."
                />
                <Switch
                  checked={form.watch("dnd_enabled")}
                  onChange={(value) =>
                    form.setValue("dnd_enabled", value, { shouldDirty: true })
                  }
                  label="DND screening"
                  description="Numbers in the do-not-disturb registry are blocked."
                />
              </div>

              <Switch
                checked={warmTransferEnabled}
                onChange={(value) =>
                  form.setValue("warm_transfer_enabled", value, { shouldDirty: true })
                }
                label="Enable human transfer"
                description="Qualified or requesting prospects can be transferred to your sales team."
              />

              {warmTransferEnabled ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field
                    label="Warm transfer number"
                    htmlFor="warm-transfer"
                    error={errors.warm_transfer_number?.message}
                    hint="Include country code, e.g. +919876543210."
                  >
                    <Input
                      id="warm-transfer"
                      placeholder="+91…"
                      {...form.register("warm_transfer_number")}
                    />
                  </Field>
                  <Field
                    label="Handoff target number"
                    htmlFor="handoff-target"
                    error={errors.handoff_target_number?.message}
                    hint="Used when a callback or briefing must reach a person."
                  >
                    <Input
                      id="handoff-target"
                      placeholder="+91…"
                      {...form.register("handoff_target_number")}
                    />
                  </Field>
                </div>
              ) : null}

              <InlineAlert tone="info" title="Interruption, silence and maximum duration">
                These runtime behaviours are enforced by the voice provider, not by this
                backend contract. Agent Studio does not expose controls that would not reach
                the provider.
              </InlineAlert>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="F · Testing and publishing"
              description="Validate configuration, test conversational flow against workspace leads, save drafts, and confirm activation."
            />
            <CardBody className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">Validation Status</span>
                    {hasErrors ? (
                      <Badge tone="danger">Needs Attention</Badge>
                    ) : (
                      <Badge tone="success">Ready to Test</Badge>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-slate-600">
                    {hasErrors
                      ? `${Object.keys(errors).length} configuration error(s) must be resolved.`
                      : "All required settings, prompt rules, and questions validated."}
                  </p>
                </div>

                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">Calling Status</span>
                    <Badge tone={business.is_active ? "success" : "neutral"}>
                      {business.is_active ? "Active" : "Draft Only"}
                    </Badge>
                  </div>
                  <p className="mt-1 text-xs text-slate-600">
                    {business.is_active
                      ? "Agent is enabled for outbound calling subject to compliance checks."
                      : "Draft mode prevents accidental outbound phone calls."}
                  </p>
                </div>

                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">Selected Voice</span>
                    <Badge tone="accent">
                      {VOICE_CATALOG.find((v) => v.id === form.watch("voice_id"))?.name || "Priya"}
                    </Badge>
                  </div>
                  <p className="mt-1 text-xs text-slate-600">
                    {Number(form.watch("speaking_speed") || 1.0).toFixed(2)}x speed · {form.watch("default_language")?.toUpperCase()} primary
                  </p>
                </div>
              </div>

              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <h4 className="text-sm font-semibold text-slate-900">In-Studio Agent Testing</h4>
                    <p className="text-xs text-slate-500 mt-0.5">
                      Simulate eligibility validation, compliance screening (Consent + DND), and AI prompt generation with real lead records.
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setTestModalOpen(true)}
                    >
                      <Sparkles className="h-4 w-4 mr-1.5 text-accent-600" />
                      Test Agent in Studio
                    </Button>
                    <ButtonLink to="/calls?new=1" variant="ghost" size="sm">
                      <ExternalLink className="h-3.5 w-3.5 mr-1" /> Custom Dry Run
                    </ButtonLink>
                    <Button
                      type="button"
                      onClick={form.handleSubmit((values) => saveMutation.mutate(values))}
                      loading={saveMutation.isPending}
                    >
                      <Save className="h-4 w-4 mr-1.5" aria-hidden /> Save draft
                    </Button>
                  </div>
                </div>
              </div>
            </CardBody>
          </Card>

          <div className="fixed inset-x-0 bottom-0 z-20 border-t border-slate-200 bg-white/95 px-4 py-3 backdrop-blur lg:left-64">
            <div className="mx-auto flex max-w-[1440px] items-center justify-between gap-3">
              <p className="text-xs text-slate-500">
                {form.formState.isDirty
                  ? "Unsaved changes · saving never activates calls"
                  : "All changes saved"}
              </p>
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => form.reset(toFormValues(business))}
                  disabled={!form.formState.isDirty || saveMutation.isPending}
                >
                  Discard
                </Button>
                <Button type="submit" loading={saveMutation.isPending}>
                  <Save className="h-4 w-4" aria-hidden /> Save draft
                </Button>
              </div>
            </div>
          </div>
        </form>
      )}

      <ConfirmDialog
        open={activationOpen}
        onClose={() => setActivationOpen(false)}
        onConfirm={() => activationMutation.mutate(!business.is_active)}
        title={business.is_active ? "Deactivate this agent?" : "Activate this agent?"}
        description={
          business.is_active ? (
            <>New outbound calls will be refused until the agent is active again.</>
          ) : (
            <>
              This makes the agent eligible for live outbound calling. Calls still pass the
              compliance gate and depend on the provider and deployment settings. Type{" "}
              <strong>ACTIVATE</strong> to continue.
            </>
          )
        }
        confirmLabel={business.is_active ? "Deactivate" : "Activate agent"}
        tone={business.is_active ? "danger" : "primary"}
        requireText={business.is_active ? undefined : "ACTIVATE"}
        loading={activationMutation.isPending}
      />

      <AgentStudioTestModal
        open={testModalOpen}
        onClose={() => setTestModalOpen(false)}
        agentName={form.watch("name") || business.name}
        language={form.watch("default_language") || "en"}
      />
    </div>
  );
}

function AgentStudioTestModal({
  open,
  onClose,
  agentName,
  language,
}: {
  open: boolean;
  onClose: () => void;
  agentName: string;
  language: string;
}) {
  const [selectedLeadId, setSelectedLeadId] = useState<string>("");
  const [isDryRun, setIsDryRun] = useState<boolean>(true);
  const [testResult, setTestResult] = useState<{
    success: boolean;
    callId?: string;
    message?: string;
  } | null>(null);

  const leadsQuery = useQuery({
    queryKey: ["leads-studio-test"],
    queryFn: () => listLeads({ limit: 50 }),
    enabled: open,
  });

  const leads = leadsQuery.data?.items ?? [];

  useEffect(() => {
    if (leads.length > 0 && !selectedLeadId) {
      setSelectedLeadId(leads[0].id);
    }
  }, [leads, selectedLeadId]);

  const testMutation = useMutation({
    mutationFn: async () => {
      if (!selectedLeadId) throw new Error("Select a lead to test with.");
      return initiateCall({
        lead_id: selectedLeadId,
        dry_run: isDryRun,
        language: language,
      });
    },
    onSuccess: (data) => {
      setTestResult({
        success: true,
        callId: data.call?.id,
        message: data.message || "Test call pipeline validated successfully via backend.",
      });
    },
    onError: (err) => {
      setTestResult({
        success: false,
        message: err instanceof Error ? err.message : "Test initiation failed.",
      });
    },
  });

  return (
    <Dialog open={open} onClose={onClose} title={`In-Studio Agent Testing: ${agentName}`}>
      <div className="space-y-4">
        <p className="text-sm text-slate-600">
          Run an end-to-end simulation of this agent against real lead context and server-side eligibility checks without dispatching unauthorized carrier traffic.
        </p>

        <div>
          <label className="label">Select Lead for Simulation Context</label>
          {leadsQuery.isPending ? (
            <Skeleton className="h-10 w-full" />
          ) : leads.length === 0 ? (
            <p className="text-xs text-amber-600">No leads available in workspace. Create a lead first.</p>
          ) : (
            <Select
              value={selectedLeadId}
              onChange={(e) => {
                setSelectedLeadId(e.target.value);
                setTestResult(null);
              }}
            >
              {leads.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name} ({l.phone}) {l.source ? `— ${l.source}` : ""}
                </option>
              ))}
            </Select>
          )}
        </div>

        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
          <Switch
            checked={isDryRun}
            onChange={setIsDryRun}
            label="Dry Run Validation (Zero Carrier Cost)"
            description="Evaluates server-side consent, DND screening, timezone window, and generates initial AI prompt without ringing the phone."
          />
        </div>

        {testResult ? (
          <div
            className={cn(
              "rounded-xl border p-4 text-sm",
              testResult.success
                ? "border-emerald-200 bg-emerald-50 text-emerald-900"
                : "border-rose-200 bg-rose-50 text-rose-900",
            )}
          >
            <div className="flex items-center gap-2 font-semibold">
              {testResult.success ? (
                <>
                  <CheckCircle2 className="h-4 w-4 text-emerald-600" /> Test Passed Successfully
                </>
              ) : (
                <>
                  <AlertTriangle className="h-4 w-4 text-rose-600" /> Test Execution Notice
                </>
              )}
            </div>
            <p className="mt-1 text-xs">{testResult.message}</p>
            {testResult.callId ? (
              <div className="mt-3 flex items-center justify-between border-t border-emerald-200/60 pt-2 text-xs">
                <span>Call ID: <code className="font-mono">{testResult.callId}</code></span>
                <ButtonLink to={`/calls/${testResult.callId}`} size="sm" variant="outline">
                  <ExternalLink className="h-3 w-3 mr-1" /> View Call Details
                </ButtonLink>
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-200">
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
          <Button
            onClick={() => testMutation.mutate()}
            loading={testMutation.isPending}
            disabled={!selectedLeadId || leads.length === 0}
          >
            <Play className="h-4 w-4 mr-1 fill-current" />
            {isDryRun ? "Run Dry Test" : "Initiate Live Call"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function AdvancedPanel({
  tools,
  toolsQuery,
  onCopy,
}: {
  tools: Array<Record<string, unknown>>;
  toolsQuery: ReturnType<typeof useQuery<Record<string, unknown>>>;
  onCopy: () => void;
}) {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Provider binding"
          description="Values that connect this business to the voice provider."
        />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <InfoRow label="Provider" value="Bolna" />
          <InfoRow label="Tool secret" value="Configured server-side (never shown)" />
          <InfoRow label="Business id" value="Injected into tool calls" mono />
          <InfoRow label="Call context" value="call_id, lead, language, knowledge" />
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Custom functions"
          description="The six tools the Bolna agent can call. Paste this definition into the provider's tool configuration."
          actions={
            <Button
              size="sm"
              variant="outline"
              onClick={onCopy}
              disabled={!toolsQuery.data}
            >
              <ClipboardCopy className="h-3.5 w-3.5" aria-hidden /> Copy JSON
            </Button>
          }
        />
        <CardBody>
          {toolsQuery.isPending ? (
            <Skeleton className="h-28 w-full" />
          ) : toolsQuery.error ? (
            <InlineAlert tone="danger" title="Tool definitions unavailable">
              The backend did not return the custom function definitions.
            </InlineAlert>
          ) : tools.length === 0 ? (
            <InlineAlert tone="warning" title="No tools exposed">
              This deployment does not expose voice tools. Follow-up questions and outcome
              capture will not work until the tools are configured.
            </InlineAlert>
          ) : (
            <>
              <ul className="mb-4 grid gap-2 sm:grid-cols-2">
                {tools.map((tool, index) => (
                  <li
                    key={String(tool.name || index)}
                    className="rounded-lg border border-slate-200 px-3 py-2"
                  >
                    <p className="font-mono text-xs font-semibold text-slate-800">
                      {String(tool.name || "unnamed")}
                    </p>
                    <p className="mt-0.5 line-clamp-2 text-xs text-slate-500">
                      {String(tool.description || "No description returned.")}
                    </p>
                  </li>
                ))}
              </ul>
              <JsonBlock title="Full tool payload" value={toolsQuery.data} />
            </>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Not exposed by this backend"
          description="Callora keeps these visibly unavailable instead of pretending."
        />
        <CardBody className="grid gap-2 text-sm text-slate-600 sm:grid-cols-2">
          {[
            "Model/provider selection from the dashboard",
            "Temperature and runtime timeout controls",
            "Voice previews and speaking-rate controls",
            "Per-agent test-call endpoint",
            "Call duration limits enforced in this service",
            "Multiple agents per business",
          ].map((item) => (
            <p key={item} className="flex items-start gap-2">
              <Info className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" aria-hidden />
              {item}
            </p>
          ))}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Follow-up answer pipeline"
          description="How live questions are grounded."
        />
        <CardBody>
          <ol className="space-y-2 text-sm text-slate-600">
            <li className="flex gap-2">
              <MessageSquareText className="mt-0.5 h-4 w-4 shrink-0 text-accent-500" />
              The provider calls the follow-up endpoint with the business and call context.
            </li>
            <li className="flex gap-2">
              <Volume2 className="mt-0.5 h-4 w-4 shrink-0 text-accent-500" />
              The retriever searches only this business's active knowledge base.
            </li>
            <li className="flex gap-2">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-accent-500" />
              If evidence is weak, the agent says it does not know rather than inventing.
            </li>
          </ol>
          <p className="mt-3 text-xs text-slate-400">
            Add verified facts in the Knowledge base tab. The exact endpoint contract is in
            the backend docs; this screen does not expose tenant secrets.
          </p>
          <div className="mt-4">
            <ButtonLink to="/agents" variant="outline" size="sm">
              Back to agents
            </ButtonLink>
          </div>
        </CardBody>
      </Card>
    </div>
  );
}

function InfoRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="rounded-lg border border-slate-200 px-3 py-2">
      <p className="text-xs text-slate-500">{label}</p>
      <p className={cn("mt-0.5 text-sm text-slate-800", mono && "font-mono text-xs")}>
        {value}
      </p>
    </div>
  );
}
