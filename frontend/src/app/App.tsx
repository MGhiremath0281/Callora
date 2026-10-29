import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Loader2, LogOut, RefreshCw, ShieldCheck } from "lucide-react";

import { useAuth } from "@/app/auth";
import { AppShell } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import HomePage from "@/pages/home";
import LoginPage from "@/pages/login";
import NotFoundPage from "@/pages/not-found";
import OverviewPage from "@/pages/overview";
import AgentsPage from "@/pages/agents";
import AgentStudioPage from "@/pages/agent-studio";
import LeadsPage from "@/pages/leads";
import LeadDetailPage from "@/pages/lead-detail";
import CallsPage from "@/pages/calls";
import CallDetailPage from "@/pages/call-detail";
import HandoffsPage from "@/pages/handoffs";
import AnalyticsPage from "@/pages/analytics";
import CompliancePage from "@/pages/compliance";
import CampaignsPage from "@/pages/campaigns";
import SettingsPage from "@/pages/settings";

function FullScreenLoader({ label }: { label: string }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-navy-950 text-white">
      <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-accent-600 shadow-lg">
        <ShieldCheck className="h-6 w-6" aria-hidden />
      </span>
      <div className="flex items-center gap-2 text-sm text-slate-300">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> {label}
      </div>
    </div>
  );
}

function AuthErrorGate() {
  const { error, refreshBusiness, logout } = useAuth();

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100 p-6">
      <Card className="w-full max-w-md p-6">
        <div className="flex items-center gap-2 text-navy-900">
          <ShieldCheck className="h-5 w-5 text-accent-600" aria-hidden />
          <p className="text-base font-semibold">Callora</p>
        </div>
        <h1 className="mt-5 text-lg font-semibold text-slate-900">
          The workspace could not be loaded
        </h1>
        <p className="mt-1 text-sm leading-relaxed text-slate-500">
          {error ||
            "The API did not return a workspace for this key. Check the backend and try again."}
        </p>
        <div className="mt-5 flex flex-wrap gap-2">
          <Button onClick={() => void refreshBusiness()}>
            <RefreshCw className="h-4 w-4" aria-hidden /> Try again
          </Button>
          <Button variant="outline" onClick={logout}>
            <LogOut className="h-4 w-4" aria-hidden /> Use a different key
          </Button>
        </div>
        <p className="mt-4 text-xs text-slate-400">
          If the backend is running on another origin, set VITE_API_BASE_URL at build time.
        </p>
      </Card>
    </div>
  );
}

function ProtectedLayout() {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return <FullScreenLoader label="Validating the workspace…" />;
  }

  if (status === "error") {
    return <AuthErrorGate />;
  }

  if (status === "anonymous") {
    if (location.pathname === "/" || location.pathname === "") {
      return <HomePage />;
    }
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: `${location.pathname}${location.search}` }}
      />
    );
  }

  return <AppShell />;
}

function LoginRoute() {
  const { status } = useAuth();
  const location = useLocation();
  const from =
    (location.state as { from?: string } | null)?.from &&
    (location.state as { from?: string }).from !== "/login"
      ? (location.state as { from?: string }).from
      : "/";

  if (status === "loading") {
    return <FullScreenLoader label="Checking your session…" />;
  }

  if (status === "authenticated") {
    return <Navigate to={from || "/"} replace />;
  }

  return <LoginPage />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/home" element={<HomePage />} />
      <Route path="/login" element={<LoginRoute />} />
      <Route element={<ProtectedLayout />}>
        <Route index element={<OverviewPage />} />
        <Route path="agents" element={<AgentsPage />} />
        <Route path="agents/:agentId" element={<AgentStudioPage />} />
        <Route path="agents/:agentId/edit" element={<AgentStudioPage />} />
        <Route path="leads" element={<LeadsPage />} />
        <Route path="leads/:leadId" element={<LeadDetailPage />} />
        <Route path="calls" element={<CallsPage />} />
        <Route path="calls/:callId" element={<CallDetailPage />} />
        <Route path="handoffs" element={<HandoffsPage />} />
        <Route path="analytics" element={<AnalyticsPage />} />
        <Route path="compliance" element={<CompliancePage />} />
        <Route path="campaigns" element={<CampaignsPage />} />
        <Route path="campaigns/:campaignId" element={<CampaignsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

export { FullScreenLoader };
export { AuthErrorGate };
export { ProtectedLayout };
export { LoginRoute };
