import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  Building2,
  CheckCircle2,
  Clock,
  Globe2,
  KeyRound,
  Layers,
  Loader2,
  LockKeyhole,
  Mail,
  Phone,
  Send,
  ShieldCheck,
  Sparkles,
  User,
} from "lucide-react";

import { useAuth } from "@/app/auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/form";
import { API_BASE } from "@/lib/api";
import { cn } from "@/lib/utils";

type Mode = "signin" | "request";

type ApiKeyApplication = {
  ticketId: string;
  companyName: string;
  contactName: string;
  workEmail: string;
  phone: string;
  industry: string;
  monthlyVolume: string;
  languages: string[];
  useCase: string;
  submittedAt: string;
};

export default function LoginPage() {
  const { login, status } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("signin");

  // Sign in state
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Request form state
  const [reqCompany, setReqCompany] = useState("");
  const [reqContact, setReqContact] = useState("");
  const [reqEmail, setReqEmail] = useState("");
  const [reqPhone, setReqPhone] = useState("");
  const [reqIndustry, setReqIndustry] = useState("Real Estate");
  const [reqVolume, setReqVolume] = useState("5,000 – 25,000 calls / month");
  const [reqLanguages, setReqLanguages] = useState<string[]>(["en", "hi"]);
  const [reqUseCase, setReqUseCase] = useState("");
  const [reqConsent, setReqConsent] = useState(false);
  const [reqSubmitting, setReqSubmitting] = useState(false);
  const [reqError, setReqError] = useState<string | null>(null);
  const [submittedTicket, setSubmittedTicket] = useState<ApiKeyApplication | null>(null);

  async function handleSignIn(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(key);
      navigate("/", { replace: true });
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "The API key could not be validated. Check the key and try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  function handleQuickDemo() {
    setKey("dev-key-biz-001");
    setError(null);
  }

  function toggleLanguage(lang: string) {
    setReqLanguages((prev) =>
      prev.includes(lang) ? prev.filter((l) => l !== lang) : [...prev, lang],
    );
  }

  async function handleRequestSubmit(event: FormEvent) {
    event.preventDefault();
    setReqError(null);

    if (!reqCompany.trim()) {
      setReqError("Please enter your registered business or company name.");
      return;
    }
    if (!reqContact.trim()) {
      setReqError("Please enter the contact person's full name.");
      return;
    }
    if (!reqEmail.trim() || !reqEmail.includes("@")) {
      setReqError("Please enter a valid business work email.");
      return;
    }
    if (!reqPhone.trim() || reqPhone.length < 8) {
      setReqError("Please provide a valid direct phone / WhatsApp number.");
      return;
    }
    if (!reqUseCase.trim() || reqUseCase.length < 20) {
      setReqError("Please provide a brief summary (at least 20 characters) explaining your sales qualification use case.");
      return;
    }
    if (!reqConsent) {
      setReqError("You must acknowledge TRAI DND and prospect consent compliance.");
      return;
    }

    setReqSubmitting(true);

    // Simulate enterprise application ingestion and ticket generation
    await new Promise((resolve) => setTimeout(resolve, 800));

    const ticketId = `CAL-REQ-${Math.floor(100000 + Math.random() * 900000)}`;
    const application: ApiKeyApplication = {
      ticketId,
      companyName: reqCompany.trim(),
      contactName: reqContact.trim(),
      workEmail: reqEmail.trim(),
      phone: reqPhone.trim(),
      industry: reqIndustry,
      monthlyVolume: reqVolume,
      languages: reqLanguages,
      useCase: reqUseCase.trim(),
      submittedAt: new Date().toISOString(),
    };

    try {
      const stored = localStorage.getItem("callora_api_key_applications");
      const list = stored ? JSON.parse(stored) : [];
      list.unshift(application);
      localStorage.setItem("callora_api_key_applications", JSON.stringify(list));
    } catch (e) {
      console.error(e);
    }

    setSubmittedTicket(application);
    setReqSubmitting(false);
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
      {/* Brand & Value Proposition Column */}
      <div className="relative hidden flex-col justify-between overflow-hidden bg-navy-900 p-10 text-white lg:flex">
        <div
          aria-hidden
          className="pointer-events-none absolute -right-32 -top-32 h-96 w-96 rounded-full bg-accent-500/20 blur-3xl"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-40 -left-24 h-96 w-96 rounded-full bg-sky-400/10 blur-3xl"
        />

        <div className="relative flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-500 text-lg font-bold">
            C
          </span>
          <div>
            <p className="text-base font-semibold">Callora</p>
            <p className="text-xs text-slate-400">Voice sales intelligence for Indian businesses</p>
          </div>
        </div>

        <div className="relative max-w-xl">
          <p className="mb-4 inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-3 py-1 text-xs text-slate-300">
            <Sparkles className="h-3.5 w-3.5 text-accent-300" aria-hidden />
            Enterprise Multilingual Outbound Calling
          </p>
          <h1 className="text-3xl font-semibold leading-tight tracking-tight sm:text-4xl">
            Every call answered, qualified and explained.
          </h1>
          <p className="mt-4 text-sm leading-relaxed text-slate-300">
            Configure conversational AI sales agents that call Indian leads, qualify prospects in
            Hindi, Kannada, and English, answer mid-call questions with grounded facts, and warm-transfer
            interested buyers to human sales teams with full briefing context.
          </p>

          <div className="mt-8 grid gap-3 sm:grid-cols-2">
            {[
              { icon: Bot, title: "Multilingual Voice AI", body: "Fluent in Hindi, Kannada, and English with live language adaptation." },
              { icon: ShieldCheck, title: "TRAI Compliance Layer", body: "Enforced consent records, DND screening, and auditable calling gates." },
              { icon: Globe2, title: "Post-Call Intelligence", body: "Instant conversation summaries, refusal extraction, and CRM sync." },
              { icon: LockKeyhole, title: "Dedicated Business Isolation", body: "Every tenant is strictly isolated by enterprise API credentials." },
            ].map((item) => (
              <div
                key={item.title}
                className="rounded-xl border border-white/10 bg-white/5 p-4 backdrop-blur-sm"
              >
                <item.icon className="h-4 w-4 text-accent-300" aria-hidden />
                <p className="mt-2 text-sm font-medium text-white">{item.title}</p>
                <p className="mt-1 text-xs leading-relaxed text-slate-400">{item.body}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="relative flex items-center justify-between text-xs text-slate-500">
          <p>Strictly backend-authoritative · Zero client-side compliance bypass.</p>
          <span className="font-mono text-slate-400">Callora v1.0</span>
        </div>
      </div>

      {/* Auth & Application Column */}
      <div className="flex items-center justify-center px-4 py-8 sm:px-8">
        <div className="w-full max-w-lg">
          {/* Mobile Header */}
          <div className="mb-6 flex items-center gap-3 lg:hidden">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-600 text-lg font-bold text-white">
              C
            </span>
            <div>
              <p className="text-base font-semibold text-slate-900">Callora</p>
              <p className="text-xs text-slate-500">Voice sales intelligence</p>
            </div>
          </div>

          {/* Mode Switcher Tabs */}
          <div className="mb-6 flex rounded-xl border border-slate-200 bg-slate-100/70 p-1">
            <button
              type="button"
              onClick={() => {
                setMode("signin");
                setSubmittedTicket(null);
              }}
              className={cn(
                "flex-1 rounded-lg py-2 text-xs font-semibold transition",
                mode === "signin"
                  ? "bg-white text-navy-950 shadow-sm"
                  : "text-slate-600 hover:text-slate-900",
              )}
            >
              Sign In with API Key
            </button>
            <button
              type="button"
              onClick={() => setMode("request")}
              className={cn(
                "flex-1 rounded-lg py-2 text-xs font-semibold transition",
                mode === "request"
                  ? "bg-white text-navy-950 shadow-sm"
                  : "text-slate-600 hover:text-slate-900",
              )}
            >
              Request Workspace Access
            </button>
          </div>

          {/* MODE 1: Standard API Key Sign In */}
          {mode === "signin" && (
            <div>
              <h2 className="text-2xl font-semibold tracking-tight text-slate-900">
                Connect your workspace
              </h2>
              <p className="mt-1 text-sm leading-relaxed text-slate-500">
                Callora authenticates every request with a business-scoped API key. The key is
                stored only in this browser and sent to the backend as the{" "}
                <code className="rounded bg-slate-100 px-1 py-0.5 text-xs font-mono">X-API-Key</code>{" "}
                header.
              </p>

              <form onSubmit={handleSignIn} className="mt-6 space-y-4" noValidate>
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label htmlFor="api-key" className="text-xs font-medium text-slate-700">
                      API key
                    </label>
                    <button
                      type="button"
                      onClick={handleQuickDemo}
                      className="text-xs text-accent-700 hover:text-accent-900 font-medium underline-offset-2 hover:underline"
                    >
                      Fill demo sandbox key
                    </button>
                  </div>
                  <div className="relative">
                    <KeyRound
                      className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
                      aria-hidden
                    />
                    <Input
                      id="api-key"
                      name="apiKey"
                      type="password"
                      autoComplete="off"
                      spellCheck={false}
                      className="pl-9 font-mono text-sm"
                      placeholder="e.g. biz_001:dev-key-biz-001"
                      value={key}
                      onChange={(event) => setKey(event.target.value)}
                      aria-invalid={Boolean(error)}
                      aria-describedby={error ? "api-key-error" : "api-key-hint"}
                    />
                  </div>
                  {error ? (
                    <p
                      id="api-key-error"
                      role="alert"
                      className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700"
                    >
                      {error}
                    </p>
                  ) : (
                    <p id="api-key-hint" className="hint">
                      The key resolves your business. It cannot read another tenant's leads,
                      calls or handoffs.
                    </p>
                  )}
                </div>

                <Button type="submit" size="lg" className="w-full" loading={submitting}>
                  {submitting ? "Validating key" : "Open workspace"}
                  {!submitting ? <ArrowRight className="h-4 w-4 ml-1" aria-hidden /> : null}
                </Button>
              </form>

              {/* Request access prompt */}
              <div className="mt-6 rounded-xl border border-slate-200 bg-slate-50 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-semibold text-slate-900">Don't have an enterprise API key?</p>
                    <p className="mt-0.5 text-xs text-slate-500">
                      Submit your business details and outbound requirements. Our team evaluates your use case, TRAI readiness, and provisions your workspace.
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setMode("request")}
                    className="shrink-0 text-xs"
                  >
                    Apply for Access
                  </Button>
                </div>
              </div>

              <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50/60 p-3.5 text-xs text-slate-500">
                <p className="font-medium text-slate-700">Developer sandbox</p>
                <p className="mt-0.5">
                  Backend seeds <code className="font-mono text-accent-700">dev-key-biz-001</code> for local evaluation. API base:{" "}
                  <code className="font-mono">{API_BASE}</code>.
                </p>
              </div>

              {status === "error" ? (
                <p className="mt-4 text-xs text-slate-400">
                  A previously stored key could not be validated. Enter it again or disconnect
                  from the profile menu.
                </p>
              ) : null}
            </div>
          )}

          {/* MODE 2: Request API Key & Workspace */}
          {mode === "request" && !submittedTicket && (
            <div>
              <div className="mb-5">
                <h2 className="text-2xl font-semibold tracking-tight text-slate-900">
                  Request Workspace API Key
                </h2>
                <p className="mt-1 text-xs text-slate-500 leading-relaxed">
                  Submit your business calling profile. Our compliance and voice engineering team evaluates
                  your use case and issues your dedicated API key within 24–48 hours.
                </p>
              </div>

              {reqError ? (
                <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs font-medium text-rose-700">
                  {reqError}
                </div>
              ) : null}

              <form onSubmit={handleRequestSubmit} className="space-y-4" noValidate>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Company / Business Name" required>
                    <div className="relative">
                      <Building2 className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                      <Input
                        className="pl-8 text-xs"
                        placeholder="e.g. UrbanNest Realty Pvt Ltd"
                        value={reqCompany}
                        onChange={(e) => setReqCompany(e.target.value)}
                        required
                      />
                    </div>
                  </Field>

                  <Field label="Industry Sector" required>
                    <Select
                      className="text-xs"
                      value={reqIndustry}
                      onChange={(e) => setReqIndustry(e.target.value)}
                    >
                      <option value="Real Estate">Real Estate & Construction</option>
                      <option value="FinTech & BFSI">FinTech, Lending & BFSI</option>
                      <option value="EdTech & Higher Ed">EdTech & Education</option>
                      <option value="Healthcare & Diagnostics">Healthcare & Clinics</option>
                      <option value="Logistics & Delivery">Logistics & Supply Chain</option>
                      <option value="B2B SaaS">B2B SaaS & Technology</option>
                      <option value="Retail & E-Commerce">Retail & E-Commerce</option>
                      <option value="Automobile">Automobile Dealerships</option>
                      <option value="Other">Other Enterprise Sector</option>
                    </Select>
                  </Field>
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Contact Person Name" required>
                    <div className="relative">
                      <User className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                      <Input
                        className="pl-8 text-xs"
                        placeholder="e.g. Rajesh Sharma"
                        value={reqContact}
                        onChange={(e) => setReqContact(e.target.value)}
                        required
                      />
                    </div>
                  </Field>

                  <Field label="Business Work Email" required>
                    <div className="relative">
                      <Mail className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                      <Input
                        type="email"
                        className="pl-8 text-xs"
                        placeholder="rajesh@urbannest.in"
                        value={reqEmail}
                        onChange={(e) => setReqEmail(e.target.value)}
                        required
                      />
                    </div>
                  </Field>
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Direct Phone / WhatsApp" required>
                    <div className="relative">
                      <Phone className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                      <Input
                        type="tel"
                        className="pl-8 text-xs"
                        placeholder="+91 98765 43210"
                        value={reqPhone}
                        onChange={(e) => setReqPhone(e.target.value)}
                        required
                      />
                    </div>
                  </Field>

                  <Field label="Expected Monthly Call Volume" required>
                    <Select
                      className="text-xs"
                      value={reqVolume}
                      onChange={(e) => setReqVolume(e.target.value)}
                    >
                      <option value="1,000 – 5,000 calls / month">1,000 – 5,000 calls / month (Pilot)</option>
                      <option value="5,000 – 25,000 calls / month">5,000 – 25,000 calls / month (Growth)</option>
                      <option value="25,000 – 100,000 calls / month">25,000 – 100,000 calls / month (Scale)</option>
                      <option value="100,000+ calls / month">100,000+ calls / month (High Throughput)</option>
                    </Select>
                  </Field>
                </div>

                <div>
                  <p className="label">Required Voice Agent Languages</p>
                  <div className="mt-1 flex flex-wrap gap-2">
                    {[
                      { code: "hi", label: "Hindi (हिंदी)" },
                      { code: "kn", label: "Kannada (ಕನ್ನಡ)" },
                      { code: "en", label: "Indian English" },
                      { code: "hinglish", label: "Hinglish (Colloquial)" },
                    ].map((l) => (
                      <button
                        key={l.code}
                        type="button"
                        onClick={() => toggleLanguage(l.code)}
                        className={cn(
                          "rounded-lg border px-3 py-1.5 text-xs font-medium transition",
                          reqLanguages.includes(l.code)
                            ? "border-accent-400 bg-accent-50 text-accent-700"
                            : "border-slate-200 bg-white text-slate-600 hover:border-slate-300",
                        )}
                      >
                        {l.label}
                      </button>
                    ))}
                  </div>
                </div>

                <Field
                  label="Sales Objective & Outbound Use Case"
                  required
                  hint="Explain why your business needs voice AI, qualification questions to ask, and handoff triggers."
                >
                  <Textarea
                    rows={3}
                    className="text-xs"
                    placeholder="e.g. We want to follow up with inbound property inquiries within 5 minutes, qualify buyer budget and preferred location, and warm-transfer interested buyers to our sales reps."
                    value={reqUseCase}
                    onChange={(e) => setReqUseCase(e.target.value)}
                  />
                </Field>

                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <Checkbox
                    checked={reqConsent}
                    onChange={(e) => setReqConsent(e.target.checked)}
                    label="Compliance & Opt-in Consent Acknowledgment"
                    description="We confirm that our outreach leads comply with TRAI DND guidelines and that verifiable prior consent is collected before outbound calling."
                  />
                </div>

                <div className="flex items-center justify-between pt-2">
                  <button
                    type="button"
                    onClick={() => setMode("signin")}
                    className="inline-flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-800"
                  >
                    <ArrowLeft className="h-3.5 w-3.5" /> Back to sign in
                  </button>
                  <Button type="submit" loading={reqSubmitting}>
                    <Send className="h-3.5 w-3.5 mr-1.5" />
                    Submit Application for Review
                  </Button>
                </div>
              </form>
            </div>
          )}

          {/* MODE 2 (SUBMITTED): Application Under Review Confirmation Card */}
          {mode === "request" && submittedTicket && (
            <div className="space-y-5">
              <div className="rounded-2xl border border-emerald-200 bg-emerald-50/60 p-6 text-center">
                <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-emerald-100 text-emerald-600">
                  <CheckCircle2 className="h-6 w-6" />
                </div>
                <h3 className="mt-3 text-lg font-semibold text-slate-900">
                  Application Under Review
                </h3>
                <p className="mt-1 text-xs text-slate-600 max-w-sm mx-auto">
                  Thank you for submitting your outbound sales profile for{" "}
                  <strong>{submittedTicket.companyName}</strong>.
                </p>

                <div className="mt-4 inline-flex items-center gap-2 rounded-full border border-emerald-300 bg-white px-3 py-1 shadow-sm">
                  <span className="text-xs text-slate-500 font-medium">Application Ref:</span>
                  <code className="font-mono text-xs font-bold text-emerald-700">
                    {submittedTicket.ticketId}
                  </code>
                </div>
              </div>

              {/* Review Timeline */}
              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">
                  Verification & Provisioning Process
                </h4>
                <ol className="relative space-y-3.5 border-l border-slate-200 ml-2.5 pl-4 text-xs">
                  <li className="relative">
                    <span className="absolute -left-[21px] top-0.5 flex h-3 w-3 items-center justify-center rounded-full bg-emerald-500 ring-4 ring-white" />
                    <p className="font-medium text-slate-900">1. Application Ingested & Profile Logged</p>
                    <p className="text-slate-500 mt-0.5">Assigned to Voice Solutions & Compliance desk.</p>
                  </li>
                  <li className="relative">
                    <span className="absolute -left-[21px] top-0.5 flex h-3 w-3 items-center justify-center rounded-full bg-amber-500 ring-4 ring-white animate-pulse" />
                    <p className="font-medium text-slate-900">2. TRAI DND & Consent Governance Evaluation</p>
                    <p className="text-slate-500 mt-0.5">Reviewing opt-in evidence integrity and target volume scope (24–48 business hours).</p>
                  </li>
                  <li className="relative">
                    <span className="absolute -left-[21px] top-0.5 flex h-3 w-3 items-center justify-center rounded-full bg-slate-300 ring-4 ring-white" />
                    <p className="font-medium text-slate-900">3. Telephony Trunk & Voice Model Provisioning</p>
                    <p className="text-slate-500 mt-0.5">Calibrating regional Indian accent models ({submittedTicket.languages.join(", ").toUpperCase()}).</p>
                  </li>
                  <li className="relative">
                    <span className="absolute -left-[21px] top-0.5 flex h-3 w-3 items-center justify-center rounded-full bg-slate-300 ring-4 ring-white" />
                    <p className="font-medium text-slate-900">4. API Key & Workspace Activation Dispatched</p>
                    <p className="text-slate-500 mt-0.5">Secure credentials sent to <strong>{submittedTicket.workEmail}</strong>.</p>
                  </li>
                </ol>
              </div>

              {/* Fast-Track Demo Sandbox Option */}
              <div className="rounded-xl border border-accent-200 bg-accent-50/50 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-semibold text-accent-900">Want to explore right now?</p>
                    <p className="mt-0.5 text-xs text-accent-700">
                      While your business application is being reviewed, you can immediately test the full dashboard in sandbox mode.
                    </p>
                  </div>
                  <Button
                    size="sm"
                    onClick={() => {
                      setMode("signin");
                      handleQuickDemo();
                    }}
                    className="shrink-0 text-xs"
                  >
                    Open Demo Sandbox
                  </Button>
                </div>
              </div>

              <div className="flex items-center justify-between pt-1">
                <button
                  type="button"
                  onClick={() => setSubmittedTicket(null)}
                  className="text-xs text-slate-500 hover:text-slate-800 underline underline-offset-2"
                >
                  Submit another application
                </button>
                <button
                  type="button"
                  onClick={() => setMode("signin")}
                  className="text-xs font-medium text-accent-700 hover:text-accent-900"
                >
                  Return to sign in →
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function AuthSplash() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50">
      <div className="flex items-center gap-3 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        Restoring workspace…
      </div>
    </div>
  );
}

