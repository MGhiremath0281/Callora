import { useEffect, useRef, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import {
  ChevronRight,
  CircleUserRound,
  LogOut,
  Menu,
  ShieldCheck,
  Wifi,
  WifiOff,
} from "lucide-react";

import { MobileSidebar, Sidebar } from "@/components/layout/sidebar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/app/auth";
import { cn } from "@/lib/utils";

const ROOT_LABELS: Record<string, string> = {
  agents: "Agents",
  leads: "Leads",
  calls: "Calls",
  handoffs: "Handoffs",
  campaigns: "Campaigns",
  analytics: "Analytics",
  compliance: "Compliance & Audit",
  settings: "Settings",
};

function Breadcrumbs() {
  const { pathname } = useLocation();
  const segments = pathname.split("/").filter(Boolean);

  if (segments.length === 0) {
    return <span className="text-sm font-medium text-slate-900">Overview</span>;
  }

  const root = segments[0];
  const rootLabel = ROOT_LABELS[root] || root;
  const rest = segments.slice(1).filter((segment) => segment !== "edit");

  return (
    <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 text-sm">
      <Link to="/" className="hidden text-slate-500 hover:text-slate-800 sm:inline">
        Workspace
      </Link>
      <ChevronRight className="hidden h-3.5 w-3.5 shrink-0 text-slate-300 sm:inline" aria-hidden />
      <Link
        to={`/${root}`}
        className={cn(
          "truncate",
          rest.length === 0
            ? "font-medium text-slate-900"
            : "text-slate-500 hover:text-slate-800",
        )}
      >
        {rootLabel}
      </Link>
      {rest.length > 0 ? (
        <>
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-slate-300" aria-hidden />
          <span className="truncate font-medium text-slate-900">
            {rest[0] === "new" ? "New" : rest[0]}
          </span>
        </>
      ) : null}
    </nav>
  );
}

function ProfileMenu() {
  const { business, apiKey, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", handler);
    return () => window.removeEventListener("mousedown", handler);
  }, [open]);

  const maskedKey = apiKey ? `${"•".repeat(8)}${apiKey.slice(-4)}` : "—";

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="flex items-center gap-2 rounded-lg p-1.5 text-sm text-slate-600 hover:bg-slate-100"
      >
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-navy-900 text-xs font-semibold text-white">
          {business?.name?.slice(0, 1).toUpperCase() || "C"}
        </span>
        <span className="hidden max-w-[160px] truncate font-medium text-slate-800 sm:block">
          {business?.name || "Workspace"}
        </span>
        <CircleUserRound className="hidden h-4 w-4 text-slate-400 sm:block" aria-hidden />
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute right-0 z-30 mt-2 w-72 rounded-xl border border-slate-200 bg-white p-2 shadow-pop animate-fade-in"
        >
          <div className="border-b border-slate-100 px-2.5 py-2">
            <p className="text-sm font-semibold text-slate-900">{business?.name}</p>
            <p className="mt-0.5 font-mono text-xs text-slate-500">{business?.business_id}</p>
          </div>
          <div className="px-2.5 py-2 text-xs text-slate-500">
            <p className="flex items-center justify-between gap-2">
              <span>API key</span>
              <span className="font-mono text-slate-700">{maskedKey}</span>
            </p>
            <p className="mt-1 flex items-center justify-between gap-2">
              <span>Scope</span>
              <span className="inline-flex items-center gap-1 text-slate-700">
                <ShieldCheck className="h-3 w-3" aria-hidden /> Business-scoped
              </span>
            </p>
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              logout();
            }}
            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-slate-600 hover:bg-slate-100 hover:text-slate-900"
          >
            <LogOut className="h-4 w-4" aria-hidden />
            Disconnect workspace
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const { business } = useAuth();
  const location = useLocation();

  useEffect(() => {
    setMobileOpen(false);
    window.scrollTo({ top: 0 });
  }, [location.pathname]);

  return (
    <div className="flex min-h-screen bg-slate-50">
      <Sidebar />
      <MobileSidebar open={mobileOpen} onClose={() => setMobileOpen(false)} />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-slate-200 bg-white/95 px-4 backdrop-blur sm:px-6">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label="Open navigation"
          >
            <Menu className="h-5 w-5" />
          </Button>
          <div className="min-w-0 flex-1">
            <Breadcrumbs />
          </div>
          <div className="hidden items-center gap-3 sm:flex">
            <Badge tone={business?.provider_configured ? "success" : "warning"}>
              {business?.provider_configured ? (
                <Wifi className="h-3 w-3" aria-hidden />
              ) : (
                <WifiOff className="h-3 w-3" aria-hidden />
              )}
              {business?.provider_configured ? "Voice provider connected" : "Provider not configured"}
            </Badge>
            {business?.outbound_calls_enabled ? (
              <Badge tone="accent">Outbound enabled</Badge>
            ) : (
              <Badge tone="neutral">Outbound disabled</Badge>
            )}
          </div>
          <ProfileMenu />
        </header>

        <main className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-6 sm:px-6 lg:py-8">
          <Outlet />
        </main>

        <footer className="border-t border-slate-200 px-6 py-4 text-xs text-slate-400">
          Callora · Backend-authoritative compliance · Recordings and transcripts require an
          authorised API key.
        </footer>
      </div>
    </div>
  );
}
