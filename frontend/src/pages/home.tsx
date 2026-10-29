import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Bot,
  Building2,
  CalendarCheck,
  CheckCircle2,
  ChevronRight,
  Clock,
  Database,
  ExternalLink,
  Flame,
  Globe2,
  HelpCircle,
  KeyRound,
  Layers,
  Lock,
  MessageSquare,
  Mic,
  Phone,
  PhoneCall,
  PhoneForwarded,
  Play,
  PlayCircle,
  RotateCcw,
  Scale,
  Send,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Square,
  TrendingUp,
  UserCheck,
  Users,
  Volume2,
  Workflow,
  Zap,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ButtonLink } from "@/components/ui/button-link";
import { cn } from "@/lib/utils";

// Voice showcase data
const VOICES = [
  {
    id: "priya",
    name: "Priya",
    language: "Indian English",
    accent: "Neutral Metro / Pan-India",
    role: "B2B SaaS & Tech Sales Qualifier",
    tone: "Consultative, articulate, confident",
    sample: "Hello! This is Priya from Callora. I noticed you requested a walkthrough of our outbound sales qualification engine. Would now be a convenient time to discuss your monthly call volume?",
    langCode: "en",
  },
  {
    id: "aarav",
    name: "Aarav",
    language: "Hindi (हिंदी)",
    accent: "Delhi-NCR / North Indian Commercial",
    role: "Real Estate & High-Ticket Inquiries",
    tone: "Polite, persuasive, natural cadence",
    sample: "नमस्ते! मैं कॉलरा से आरव बात कर रहा हूँ। क्या आप 3BHK विला और प्रीमियम रेजिडेंशियल प्रोजेक्ट्स के नए फेज में इन्वेस्टमन्ट एक्सप्लोर करना चाहेंगे?",
    langCode: "hi",
  },
  {
    id: "kavya",
    name: "Kavya",
    language: "Kannada (ಕನ್ನಡ)",
    accent: "Bengaluru / Southern Regional",
    role: "SMB Logistics & Regional Outreach",
    tone: "Friendly, courteous, local fluency",
    sample: "ನಮಸ್ಕಾರ! ನಾನು ಕಾಲೋರಾದಿಂದ ಕಾವ್ಯ ಮಾತನಾಡುತ್ತಿದ್ದೇನೆ. ನಿಮ್ಮ ಕಂಪನಿಯ ಲಾಜಿಸ್ಟಿಕ್ಸ್ ಮತ್ತು ಮಾರಾಟ ವಿಭಾಗಕ್ಕೆ ನಮ್ಮ ಧ್ವನಿ ಪರಿಹಾರ ಹೇಗೆ ಸಹಾಯ ಮಾಡಬಹುದು ಎಂದು ಚರ್ಚಿಸೋಣವೇ?",
    langCode: "kn",
  },
  {
    id: "ananya",
    name: "Ananya",
    language: "Hinglish (Colloquial)",
    accent: "Urban Metro Mix",
    role: "FinTech & Direct Inbound Fast-Response",
    tone: "Dynamic, modern, conversational",
    sample: "Hi there! Main Ananya from Callora. We saw your loan eligibility inquiry. Just checking if you have 2 minutes to confirm your business turnover and required tenure?",
    langCode: "hi",
  },
];

// Interactive mock call transcript scenario
const SIMULATED_CALL_TURNS = [
  {
    speaker: "agent",
    name: "Callora AI (Aarav)",
    lang: "Hindi",
    time: "00:03",
    text: "नमस्ते विक्रम जी! मैं अर्बननेस्ट रियल्टी से कॉलरा एआई असिस्टेंट आरव बात कर रहा हूँ। आपने हमारी वेबसाइट पर 3BHK लक्ज़री अपार्टमेंट्स के लिए इंक्वायरी की थी। क्या यह बात करने का सही समय है?",
  },
  {
    speaker: "lead",
    name: "Vikram Joshi (Prospect)",
    lang: "Hinglish",
    time: "00:14",
    text: "Haanji bolie. Actually main Whitefield ya Sarjapur area mein dekh raha hoon. Budget around 1.2 Crore hai.",
  },
  {
    speaker: "agent",
    name: "Callora AI (Aarav)",
    lang: "Hinglish / Switching",
    time: "00:26",
    text: "That's great! Whitefield mein hamare paas 2 upcoming RERA-approved gated communities hain jo aapke 1.2 Cr budget mein bilkul fit hoti hain. Possession mid-2026 hai. Kya aap weekend par site visit plan kar sakte hain?",
  },
  {
    speaker: "lead",
    name: "Vikram Joshi (Prospect)",
    lang: "English",
    time: "00:39",
    text: "Yes, Saturday morning 11 AM works for me. Can someone share floor plans and price breakdown on WhatsApp?",
  },
  {
    speaker: "agent",
    name: "Callora AI (Aarav)",
    lang: "English",
    time: "00:48",
    text: "Confirmed! I have locked in Saturday 11 AM and our senior property advisor Rohit will assist you. I'm transferring you to him right now for the floor plans.",
  },
];

export default function HomePage() {
  const [activeVoice, setActiveVoice] = useState(VOICES[0]);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playingVoiceId, setPlayingVoiceId] = useState<string | null>(null);

  const playVoice = (voice: typeof VOICES[0]) => {
    setActiveVoice(voice);

    if (isPlaying && playingVoiceId === voice.id) {
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }
      setIsPlaying(false);
      setPlayingVoiceId(null);
      return;
    }

    setIsPlaying(true);
    setPlayingVoiceId(voice.id);

    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(voice.sample);
      utterance.rate = 1.0;
      utterance.onend = () => {
        setIsPlaying(false);
        setPlayingVoiceId(null);
      };
      utterance.onerror = () => {
        setIsPlaying(false);
        setPlayingVoiceId(null);
      };
      window.speechSynthesis.speak(utterance);
    } else {
      setTimeout(() => {
        setIsPlaying(false);
        setPlayingVoiceId(null);
      }, 4000);
    }
  };

  return (
    <div className="min-h-screen bg-[#070A13] text-slate-100 selection:bg-accent-500/30 selection:text-accent-200">
      {/* Ambient background glow & grid canvas */}
      <div className="fixed inset-0 pointer-events-none overflow-hidden">
        <div className="absolute -top-40 left-1/2 -translate-x-1/2 w-[1000px] h-[550px] bg-gradient-to-b from-indigo-600/20 via-violet-600/10 to-transparent blur-3xl opacity-70" />
        <div className="absolute top-[40%] -left-40 w-[600px] h-[500px] bg-sky-600/10 blur-3xl rounded-full" />
        <div className="absolute top-[60%] -right-40 w-[600px] h-[500px] bg-emerald-600/10 blur-3xl rounded-full" />
        <div
          className="absolute inset-0 opacity-[0.03]"
          style={{
            backgroundImage: `radial-gradient(circle at 1px 1px, #ffffff 1px, transparent 0)`,
            backgroundSize: "32px 32px",
          }}
        />
      </div>

      {/* Top Navigation */}
      <header className="relative z-30 border-b border-white/[0.08] bg-[#070A13]/80 backdrop-blur-xl sticky top-0">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
          <Link to="/" className="flex items-center gap-3 group">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-accent-500 to-indigo-600 font-bold text-white shadow-lg shadow-accent-500/25 transition group-hover:scale-105">
              C
            </span>
            <div>
              <span className="font-semibold tracking-tight text-white flex items-center gap-2">
                Callora
                <span className="rounded-full bg-accent-500/10 px-2 py-0.5 text-[10px] font-semibold text-accent-300 border border-accent-500/20">
                  Voice AI
                </span>
              </span>
            </div>
          </Link>

          <nav className="hidden md:flex items-center gap-8 text-sm font-medium text-slate-300">
            <a href="#problem" className="hover:text-white transition">The Problem</a>
            <a href="#solution" className="hover:text-white transition">How It Works</a>
            <a href="#voices" className="hover:text-white transition">Voice Studio</a>
            <a href="#compliance" className="hover:text-white transition">TRAI Compliance</a>
            <a href="#comparison" className="hover:text-white transition">Why Callora</a>
          </nav>

          <div className="flex items-center gap-3">
            <Link
              to="/login"
              className="text-xs sm:text-sm font-medium text-slate-300 hover:text-white px-3 py-1.5 transition"
            >
              Sign In
            </Link>
            <Link
              to="/login"
              className="inline-flex items-center gap-1.5 rounded-xl bg-white text-navy-950 px-4 py-2 text-xs sm:text-sm font-semibold hover:bg-slate-100 transition shadow-sm hover:shadow-white/10"
            >
              Request Access <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
        </div>
      </header>

      {/* Hero Section */}
      <section className="relative z-10 pt-20 pb-28 px-4 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-5xl text-center">
          {/* Eyebrow Badge */}
          <div className="inline-flex items-center gap-2 rounded-full border border-accent-500/30 bg-accent-500/10 px-3.5 py-1.5 text-xs font-medium text-accent-200 mb-8 backdrop-blur-md">
            <Sparkles className="h-3.5 w-3.5 text-accent-400" />
            <span>Built for Indian Outbound Sales Teams & TRAI Compliance</span>
          </div>

          {/* Main Headline */}
          <h1 className="text-4xl sm:text-6xl lg:text-7xl font-bold tracking-tight text-white leading-[1.1] max-w-4xl mx-auto">
            Automated Sales Calling That{" "}
            <span className="bg-gradient-to-r from-accent-300 via-sky-300 to-indigo-300 bg-clip-text text-transparent">
              Sounds Human
            </span>{" "}
            and Understands Indian Dialects.
          </h1>

          {/* Subtitle */}
          <p className="mt-6 text-lg sm:text-xl text-slate-300 max-w-2xl mx-auto leading-relaxed font-normal">
            Callora empowers Indian enterprises to qualify inbound leads within 30 seconds.
            Converses naturally in <strong className="text-white">Hindi, Kannada, and Indian English</strong>,
            answers questions with verified knowledge, and warm-transfers high-intent prospects to human sales closers.
          </p>

          {/* Action Button Row */}
          <div className="mt-10 flex flex-wrap items-center justify-center gap-4">
            <Link
              to="/login"
              className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-accent-600 to-indigo-600 px-6 py-3.5 text-sm font-semibold text-white shadow-xl shadow-accent-600/25 hover:from-accent-500 hover:to-indigo-500 transition-all hover:scale-[1.02]"
            >
              Request Workspace API Key
              <ArrowRight className="h-4 w-4" />
            </Link>
            <Link
              to="/login"
              className="inline-flex items-center gap-2 rounded-xl border border-white/15 bg-white/5 px-6 py-3.5 text-sm font-semibold text-slate-200 backdrop-blur-md hover:bg-white/10 hover:border-white/25 transition"
            >
              <KeyRound className="h-4 w-4 text-accent-400" />
              Test Sandbox Demo
            </Link>
          </div>

          {/* Compliance & Trust Markers */}
          <div className="mt-12 flex flex-wrap items-center justify-center gap-8 text-xs text-slate-400">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-emerald-400" />
              <span>TRAI DND Registry Screening</span>
            </div>
            <div className="flex items-center gap-2">
              <Zap className="h-4 w-4 text-amber-400" />
              <span>Sub-600ms Voice Latency</span>
            </div>
            <div className="flex items-center gap-2">
              <PhoneForwarded className="h-4 w-4 text-sky-400" />
              <span>Live SIP Human Warm Transfer</span>
            </div>
            <div className="flex items-center gap-2">
              <Database className="h-4 w-4 text-violet-400" />
              <span>Grounded Knowledge Base (Zero Hallucinations)</span>
            </div>
          </div>
        </div>

        {/* Interactive Simulated Live Call Preview Widget */}
        <div className="mx-auto max-w-4xl mt-16 rounded-2xl border border-white/10 bg-[#0F1422]/90 p-5 sm:p-7 shadow-2xl backdrop-blur-2xl ring-1 ring-white/5">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-5 border-b border-white/10 gap-3">
            <div className="flex items-center gap-3">
              <div className="relative flex h-10 w-10 items-center justify-center rounded-full bg-emerald-500/20 text-emerald-400 ring-2 ring-emerald-500/30">
                <PhoneCall className="h-5 w-5 animate-pulse" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="font-semibold text-sm text-white">Live Qualification Call Simulation</h3>
                  <Badge tone="success" className="bg-emerald-500/20 text-emerald-300 border-0">
                    Active Call
                  </Badge>
                </div>
                <p className="text-xs text-slate-400">Prospect: Vikram Joshi · Bengaluru · Source: Inbound 3BHK Campaign</p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <span className="rounded-lg bg-white/5 border border-white/10 px-2.5 py-1 text-xs font-mono text-slate-300">
                00:54 · Hindi ⇄ English
              </span>
              <span className="rounded-lg bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1 text-xs font-semibold text-emerald-400 flex items-center gap-1">
                <Flame className="h-3.5 w-3.5" /> High Intent (94%)
              </span>
            </div>
          </div>

          {/* Transcript Dialogue */}
          <div className="py-6 space-y-4 max-h-[340px] overflow-y-auto pr-2 scrollbar-thin">
            {SIMULATED_CALL_TURNS.map((turn, i) => (
              <div
                key={i}
                className={cn(
                  "flex flex-col gap-1 rounded-xl p-3.5 text-xs sm:text-sm leading-relaxed border transition",
                  turn.speaker === "agent"
                    ? "bg-accent-950/30 border-accent-500/20 text-slate-200 ml-0 mr-8 sm:mr-16"
                    : "bg-white/[0.03] border-white/10 text-slate-300 ml-8 sm:ml-16 mr-0",
                )}
              >
                <div className="flex items-center justify-between text-[11px] text-slate-400 mb-0.5">
                  <div className="flex items-center gap-2 font-medium">
                    <span className={turn.speaker === "agent" ? "text-accent-300 font-semibold" : "text-sky-300 font-semibold"}>
                      {turn.name}
                    </span>
                    <span className="rounded bg-white/10 px-1.5 py-0.2 text-[10px] text-slate-300">
                      {turn.lang}
                    </span>
                  </div>
                  <span className="font-mono text-slate-500">{turn.time}</span>
                </div>
                <p>{turn.text}</p>
              </div>
            ))}
          </div>

          {/* Real-time Post-Call Intelligence Briefing Strip */}
          <div className="mt-4 pt-4 border-t border-white/10 grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
            <div className="rounded-lg bg-white/[0.02] border border-white/5 p-2.5">
              <span className="text-slate-400 block text-[11px]">Budget Captured</span>
              <span className="font-semibold text-white">₹1.20 Crore</span>
            </div>
            <div className="rounded-lg bg-white/[0.02] border border-white/5 p-2.5">
              <span className="text-slate-400 block text-[11px]">Target Location</span>
              <span className="font-semibold text-white">Whitefield, Bengaluru</span>
            </div>
            <div className="rounded-lg bg-white/[0.02] border border-white/5 p-2.5">
              <span className="text-slate-400 block text-[11px]">Scheduled Visit</span>
              <span className="font-semibold text-emerald-400">Saturday 11:00 AM</span>
            </div>
            <div className="rounded-lg bg-white/[0.02] border border-white/5 p-2.5">
              <span className="text-slate-400 block text-[11px]">Next Sales Action</span>
              <span className="font-semibold text-accent-300">Warm Transfer to Rohit</span>
            </div>
          </div>
        </div>
      </section>

      {/* The Problem Section */}
      <section id="problem" className="relative z-10 py-24 border-t border-white/[0.08] bg-[#0A0E1A]/60">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="max-w-3xl mb-16">
            <Badge tone="accent" className="bg-accent-500/10 text-accent-300 border border-accent-500/20 mb-3">
              The Indian Outbound Dilemma
            </Badge>
            <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-white leading-tight">
              Why Traditional Sales Calling Fails in Indian Businesses
            </h2>
            <p className="mt-4 text-slate-400 leading-relaxed">
              Companies spend lakhs on Facebook, Google, and 99acres ads, only to lose 70% of high-intent buyers
              due to delayed follow-ups, dialect mismatch, and regulatory risk.
            </p>
          </div>

          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-2xl border border-rose-500/20 bg-rose-950/10 p-6 backdrop-blur-sm">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-rose-500/20 text-rose-400 mb-4">
                <Clock className="h-5 w-5" />
              </div>
              <h3 className="font-semibold text-white text-base">The 15-Minute Lead Decay</h3>
              <p className="mt-2 text-xs sm:text-sm text-slate-400 leading-relaxed">
                Leads called after 15 minutes have an 80% lower qualification rate. Human sales reps cannot dial hundreds of daily web inquiries instantly.
              </p>
            </div>

            <div className="rounded-2xl border border-amber-500/20 bg-amber-950/10 p-6 backdrop-blur-sm">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-500/20 text-amber-400 mb-4">
                <Globe2 className="h-5 w-5" />
              </div>
              <h3 className="font-semibold text-white text-base">Language & Accent Barrier</h3>
              <p className="mt-2 text-xs sm:text-sm text-slate-400 leading-relaxed">
                Western voice bots stumble on Indian pronunciations, regional accents, and colloquial Hinglish. Prospects hang up in the first 5 seconds.
              </p>
            </div>

            <div className="rounded-2xl border border-sky-500/20 bg-sky-950/10 p-6 backdrop-blur-sm">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-sky-500/20 text-sky-400 mb-4">
                <ShieldAlert className="h-5 w-5" />
              </div>
              <h3 className="font-semibold text-white text-base">TRAI DND Fines & Number Blocks</h3>
              <p className="mt-2 text-xs sm:text-sm text-slate-400 leading-relaxed">
                Unscreened dialling risks TRAI penalties, telecom blacklisting, and brand reputational damage. Compliance cannot be an afterthought.
              </p>
            </div>

            <div className="rounded-2xl border border-violet-500/20 bg-violet-950/10 p-6 backdrop-blur-sm">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-500/20 text-violet-400 mb-4">
                <RotateCcw className="h-5 w-5" />
              </div>
              <h3 className="font-semibold text-white text-base">High SDR Attrition & Cost</h3>
              <p className="mt-2 text-xs sm:text-sm text-slate-400 leading-relaxed">
                Hiring, training, and retaining human cold-calling reps costs thousands of dollars per month with unpredictable call quality and poor logging.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* How Callora Works Section */}
      <section id="solution" className="relative z-10 py-24 border-t border-white/[0.08]">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="text-center max-w-3xl mx-auto mb-16">
            <Badge tone="accent" className="bg-accent-500/10 text-accent-300 border border-accent-500/20 mb-3">
              The Architecture
            </Badge>
            <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-white">
              How Callora Solves Outbound Calling End-to-End
            </h2>
            <p className="mt-4 text-slate-400 text-sm sm:text-base">
              A unified pipeline integrating lead ingestion, regulatory screening, multilingual telephony, and human handoff.
            </p>
          </div>

          <div className="grid gap-6 md:grid-cols-3">
            <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-7 backdrop-blur-md">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-accent-500/20 text-accent-400 mb-5">
                <ShieldCheck className="h-6 w-6" />
              </div>
              <span className="text-xs font-mono text-accent-400 uppercase tracking-wider font-semibold">Step 01</span>
              <h3 className="text-lg font-semibold text-white mt-1">Preflight Compliance Gate</h3>
              <p className="mt-2 text-xs sm:text-sm text-slate-400 leading-relaxed">
                Every lead passes through our server-side eligibility check. Callora verifies opt-in consent records, screens the national TRAI DND registry, checks timezone windows, and enforces daily quotas.
              </p>
            </div>

            <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-7 backdrop-blur-md">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-indigo-500/20 text-indigo-400 mb-5">
                <Bot className="h-6 w-6" />
              </div>
              <span className="text-xs font-mono text-indigo-400 uppercase tracking-wider font-semibold">Step 02</span>
              <h3 className="text-lg font-semibold text-white mt-1">Autonomous Multilingual Qualification</h3>
              <p className="mt-2 text-xs sm:text-sm text-slate-400 leading-relaxed">
                The agent initiates the call in under 30 seconds. Discloses AI identity, answers objections with your grounded knowledge base, captures qualification answers, and adapts mid-call if the lead speaks Hindi or Kannada.
              </p>
            </div>

            <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-7 backdrop-blur-md">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-emerald-500/20 text-emerald-400 mb-5">
                <PhoneForwarded className="h-6 w-6" />
              </div>
              <span className="text-xs font-mono text-emerald-400 uppercase tracking-wider font-semibold">Step 03</span>
              <h3 className="text-lg font-semibold text-white mt-1">Live Warm Transfer & Post-Call Intel</h3>
              <p className="mt-2 text-xs sm:text-sm text-slate-400 leading-relaxed">
                Interested prospects are instantly transferred to your human closers via SIP. Unanswered calls receive retry scheduling, while completed calls generate AI summaries, refusal patterns, and CRM sync.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Voice Showcase Section */}
      <section id="voices" className="relative z-10 py-24 border-t border-white/[0.08] bg-[#0A0E1A]/70">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="max-w-3xl mb-12">
            <Badge tone="accent" className="bg-accent-500/10 text-accent-300 border border-accent-500/20 mb-3">
              Voice Studio & Dialects
            </Badge>
            <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-white">
              Listen to Callora Voice Models
            </h2>
            <p className="mt-4 text-slate-400 text-sm sm:text-base">
              Tuned for Indian conversational pacing, natural inflection, and regional dialects. Click below to preview live audio samples.
            </p>
          </div>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {VOICES.map((voice) => {
              const active = activeVoice.id === voice.id;
              const playing = isPlaying && playingVoiceId === voice.id;
              return (
                <div
                  key={voice.id}
                  onClick={() => playVoice(voice)}
                  className={cn(
                    "cursor-pointer rounded-2xl border p-5 transition-all text-left flex flex-col justify-between",
                    active
                      ? "border-accent-500 bg-accent-950/20 shadow-lg shadow-accent-500/10 ring-1 ring-accent-500/30"
                      : "border-white/10 bg-white/[0.02] hover:border-white/20 hover:bg-white/[0.04]",
                  )}
                >
                  <div>
                    <div className="flex items-center justify-between mb-3">
                      <div>
                        <h4 className="font-semibold text-white text-base">{voice.name}</h4>
                        <span className="text-xs text-accent-300 font-medium">{voice.language}</span>
                      </div>
                      <button
                        type="button"
                        className={cn(
                          "flex h-9 w-9 items-center justify-center rounded-full transition shadow",
                          playing
                            ? "bg-accent-500 text-white animate-pulse"
                            : "bg-white/10 text-white hover:bg-accent-500",
                        )}
                        aria-label={playing ? "Stop preview" : "Play preview"}
                      >
                        {playing ? <Square className="h-3.5 w-3.5 fill-current" /> : <Play className="h-3.5 w-3.5 fill-current ml-0.5" />}
                      </button>
                    </div>

                    <p className="text-xs text-slate-400 mt-2">{voice.role}</p>
                    <p className="text-[11px] text-slate-500 italic mt-1">{voice.tone}</p>
                  </div>

                  <div className="mt-4 pt-3 border-t border-white/10 flex items-center justify-between text-[11px] text-slate-400">
                    <span>{voice.accent}</span>
                    <span className="font-mono text-accent-400">{playing ? "Playing..." : "Click to test"}</span>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Active Voice Script Display Box */}
          <div className="mt-8 rounded-2xl border border-white/10 bg-[#0F1424] p-5 sm:p-6">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                Sample Call Script · {activeVoice.name} ({activeVoice.language})
              </span>
              <button
                type="button"
                onClick={() => playVoice(activeVoice)}
                className="inline-flex items-center gap-1.5 text-xs text-accent-300 hover:text-accent-200 font-medium"
              >
                <Volume2 className="h-4 w-4" /> Replay Sample
              </button>
            </div>
            <p className="text-sm sm:text-base text-slate-200 leading-relaxed font-normal">
              "{activeVoice.sample}"
            </p>
          </div>
        </div>
      </section>

      {/* Compliance & Audit Section */}
      <section id="compliance" className="relative z-10 py-24 border-t border-white/[0.08]">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="grid gap-12 lg:grid-cols-2 lg:items-center">
            <div>
              <Badge tone="success" className="bg-emerald-500/10 text-emerald-300 border border-emerald-500/20 mb-3">
                TRAI & Regulatory Governance
              </Badge>
              <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-white leading-tight">
                Designed From Day 1 to Keep Outbound Calling 100% Legal & Auditable
              </h2>
              <p className="mt-4 text-slate-400 text-sm sm:text-base leading-relaxed">
                Most telemarketing voice bots operate in regulatory grey areas. Callora is built with a strict
                server-side compliance engine that cannot be bypassed by frontend scripts or campaign creators.
              </p>

              <div className="mt-8 space-y-4">
                {[
                  {
                    title: "Per-Lead Consent Records with Audit Trail",
                    desc: "Every callable lead must possess timestamped proof of prior opt-in consent (web form, SMS, or inbound request). Missing consent blocks dialling automatically.",
                  },
                  {
                    title: "National DND Registry Auto-Screening",
                    desc: "Phone numbers registered in the telecom Do Not Disturb (DND) list are filtered before call queues are dispatched.",
                  },
                  {
                    title: "Strict Telecom Calling Windows & Daily Quotas",
                    desc: "Restricts dialling to legal business hours (09:00 to 20:00) in recipient timezones, preventing nuisance calls and carrier rate limits.",
                  },
                  {
                    title: "Mandatory AI Disclosure Compliance",
                    desc: "Agent opens every outbound call by transparently stating its identity as an automated AI sales assistant on behalf of your brand.",
                  },
                ].map((item, idx) => (
                  <div key={idx} className="flex gap-3">
                    <CheckCircle2 className="h-5 w-5 text-emerald-400 shrink-0 mt-0.5" />
                    <div>
                      <h4 className="text-sm font-semibold text-white">{item.title}</h4>
                      <p className="text-xs text-slate-400 mt-0.5 leading-relaxed">{item.desc}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Compliance Inspector Visual Card */}
            <div className="rounded-2xl border border-white/10 bg-[#0F1424]/90 p-6 shadow-2xl backdrop-blur-xl">
              <div className="flex items-center justify-between pb-4 border-b border-white/10">
                <div className="flex items-center gap-2">
                  <ShieldCheck className="h-5 w-5 text-emerald-400" />
                  <span className="font-semibold text-sm text-white">Callora Compliance Decision Gate</span>
                </div>
                <Badge tone="success" className="bg-emerald-500/20 text-emerald-300">
                  Passed · Verdict: ALLOWED
                </Badge>
              </div>

              <div className="mt-4 space-y-3 font-mono text-xs">
                <div className="flex justify-between p-2.5 rounded-lg bg-white/[0.03] border border-white/5">
                  <span className="text-slate-400">Target Phone:</span>
                  <span className="text-slate-200">+91 98450 •••••</span>
                </div>
                <div className="flex justify-between p-2.5 rounded-lg bg-white/[0.03] border border-white/5">
                  <span className="text-slate-400">Consent Verification:</span>
                  <span className="text-emerald-400 font-semibold">VERIFIED (Source: Inbound Webform)</span>
                </div>
                <div className="flex justify-between p-2.5 rounded-lg bg-white/[0.03] border border-white/5">
                  <span className="text-slate-400">National DND Registry:</span>
                  <span className="text-emerald-400 font-semibold">CLEARED (Not on DND)</span>
                </div>
                <div className="flex justify-between p-2.5 rounded-lg bg-white/[0.03] border border-white/5">
                  <span className="text-slate-400">Window Check (IST):</span>
                  <span className="text-emerald-400 font-semibold">14:32 IST (Within 09:00-20:00)</span>
                </div>
                <div className="flex justify-between p-2.5 rounded-lg bg-white/[0.03] border border-white/5">
                  <span className="text-slate-400">Daily Lead Attempt Quota:</span>
                  <span className="text-emerald-400 font-semibold">1 / 3 attempts today</span>
                </div>
              </div>

              <div className="mt-4 pt-3 border-t border-white/10 flex items-center justify-between text-[11px] text-slate-400">
                <span>Cryptographic Audit ID:</span>
                <span className="font-mono text-accent-300">dec_7f9a2e8c1</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Comparison Table Section */}
      <section id="comparison" className="relative z-10 py-24 border-t border-white/[0.08] bg-[#0A0E1A]/80">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="text-center max-w-3xl mx-auto mb-16">
            <Badge tone="accent" className="bg-accent-500/10 text-accent-300 border border-accent-500/20 mb-3">
              Market Comparison
            </Badge>
            <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-white">
              Why Indian Companies Choose Callora
            </h2>
            <p className="mt-4 text-slate-400 text-sm sm:text-base">
              Comparing human BPO call centers, generic Western voice bots, and Callora's specialized Indian engine.
            </p>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs sm:text-sm border-collapse">
              <thead>
                <tr className="border-b border-white/10 text-slate-400">
                  <th className="py-4 px-4 font-semibold">Capability</th>
                  <th className="py-4 px-4 font-semibold">Human SDR BPO</th>
                  <th className="py-4 px-4 font-semibold">Generic US Voice Bots</th>
                  <th className="py-4 px-4 font-semibold text-accent-300 bg-accent-500/10 rounded-t-lg">
                    Callora Voice AI
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5 text-slate-300">
                <tr>
                  <td className="py-3.5 px-4 font-medium text-white">Dialling Speed</td>
                  <td className="py-3.5 px-4 text-slate-400">15 min – 4 hours</td>
                  <td className="py-3.5 px-4 text-slate-400">Instant</td>
                  <td className="py-3.5 px-4 font-semibold text-emerald-400 bg-accent-500/5">&lt; 30 Seconds Instant</td>
                </tr>
                <tr>
                  <td className="py-3.5 px-4 font-medium text-white">Hindi & Kannada Fluency</td>
                  <td className="py-3.5 px-4 text-slate-400">Depends on rep</td>
                  <td className="py-3.5 px-4 text-rose-400">Robotic / Inaccurate</td>
                  <td className="py-3.5 px-4 font-semibold text-emerald-400 bg-accent-500/5">Native + Mid-Call Switch</td>
                </tr>
                <tr>
                  <td className="py-3.5 px-4 font-medium text-white">TRAI DND Screening</td>
                  <td className="py-3.5 px-4 text-slate-400">Manual / Error-prone</td>
                  <td className="py-3.5 px-4 text-rose-400">None (US FCC only)</td>
                  <td className="py-3.5 px-4 font-semibold text-emerald-400 bg-accent-500/5">Automated Preflight Gate</td>
                </tr>
                <tr>
                  <td className="py-3.5 px-4 font-medium text-white">Live SIP Human Transfer</td>
                  <td className="py-3.5 px-4 text-slate-400">Requires conference bridge</td>
                  <td className="py-3.5 px-4 text-slate-400">Unsupported in India</td>
                  <td className="py-3.5 px-4 font-semibold text-emerald-400 bg-accent-500/5">Seamless Hot Transfer</td>
                </tr>
                <tr>
                  <td className="py-3.5 px-4 font-medium text-white">Cost per Qualified Lead</td>
                  <td className="py-3.5 px-4 text-rose-400">₹800 – ₹2,500</td>
                  <td className="py-3.5 px-4 text-slate-400">High USD Telephony</td>
                  <td className="py-3.5 px-4 font-semibold text-emerald-400 bg-accent-500/5">70% Lower Cost</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* Target Industries */}
      <section className="relative z-10 py-24 border-t border-white/[0.08]">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="text-center max-w-3xl mx-auto mb-16">
            <h2 className="text-3xl font-bold tracking-tight text-white">
              Built for High-Growth Indian Verticals
            </h2>
            <p className="mt-3 text-slate-400 text-sm">
              Tailored outbound playbooks and conversation qualification flows.
            </p>
          </div>

          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {[
              {
                title: "Real Estate & Housing",
                desc: "Qualify buyers on budget, BHK requirement, and target micro-market. Automatically book weekend on-site property walkthroughs.",
              },
              {
                title: "FinTech & Lending",
                desc: "Verify business turnover, loan requirement, and credit history before routing pre-qualified borrowers to credit underwriters.",
              },
              {
                title: "EdTech & Universities",
                desc: "Call program applicants, identify career aspirations, and book counseling calls for degree and certification courses.",
              },
              {
                title: "Healthcare & Clinics",
                desc: "Follow up on elective surgery inquiries, confirm consultation slots, and verify insurance pre-qualification.",
              },
              {
                title: "Logistics & Fleet",
                desc: "Onboard transport vendors, verify fleet availability, and qualify logistics tender inquiries across states.",
              },
              {
                title: "B2B SaaS & Enterprise",
                desc: "Follow up with whitepaper downloads, qualify company seat counts, and warm-transfer executives to Account Executives.",
              },
            ].map((sector, i) => (
              <div
                key={i}
                className="rounded-xl border border-white/10 bg-white/[0.02] p-6 hover:border-white/20 transition backdrop-blur-sm"
              >
                <h4 className="font-semibold text-white text-base">{sector.title}</h4>
                <p className="mt-2 text-xs sm:text-sm text-slate-400 leading-relaxed">{sector.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Final CTA Banner */}
      <section className="relative z-10 py-20 border-t border-white/[0.08] bg-gradient-to-b from-accent-950/20 via-[#0B0F1E] to-[#070A13]">
        <div className="mx-auto max-w-5xl px-4 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-accent-500 text-white font-bold text-xl mx-auto shadow-xl shadow-accent-500/30 mb-6">
            C
          </span>
          <h2 className="text-3xl sm:text-5xl font-bold tracking-tight text-white leading-tight">
            Ready to Supercharge Your Indian Sales Calls?
          </h2>
          <p className="mt-4 text-base sm:text-lg text-slate-300 max-w-xl mx-auto">
            Get your dedicated workspace API key, configure multilingual agents, and start qualifying leads within minutes.
          </p>

          <div className="mt-8 flex flex-wrap items-center justify-center gap-4">
            <Link
              to="/login"
              className="rounded-xl bg-white text-navy-950 px-6 py-3.5 text-sm font-semibold hover:bg-slate-100 transition shadow-lg"
            >
              Request Workspace Access
            </Link>
            <Link
              to="/login"
              className="rounded-xl border border-white/20 bg-white/5 px-6 py-3.5 text-sm font-semibold text-white hover:bg-white/10 transition"
            >
              Sign In with Existing Key
            </Link>
          </div>
        </div>
      </section>

      {/* Modern Footer */}
      <footer className="relative z-10 border-t border-white/[0.08] bg-[#05070D] py-12 text-xs text-slate-500">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 flex flex-col sm:flex-row items-center justify-between gap-6">
          <div className="flex items-center gap-3">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent-600 text-xs font-bold text-white">
              C
            </span>
            <span className="font-semibold text-slate-300">Callora</span>
            <span>— Multilingual Voice Sales Agent for Indian Businesses</span>
          </div>

          <div className="flex items-center gap-6">
            <Link to="/home" className="hover:text-slate-300 transition">Overview</Link>
            <a href="#compliance" className="hover:text-slate-300 transition">TRAI Compliance</a>
            <a href="#voices" className="hover:text-slate-300 transition">Voice Studio</a>
            <Link to="/login" className="hover:text-slate-300 transition">Sign In</Link>
          </div>

          <p>© {new Date().getFullYear()} Callora. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
