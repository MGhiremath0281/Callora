import { Link, NavLink, useLocation } from "react-router-dom";
import {
  BarChart3,
  Bot,
  Building2,
  CalendarClock,
  ChevronLeft,
  ExternalLink,
  Globe2,
  LayoutDashboard,
  Megaphone,
  PhoneCall,
  Settings,
  ShieldCheck,
  Users,
  X,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

type NavItem = {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
};

type NavGroup = {
  label: string;
  items: NavItem[];
};

export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Workspace",
    items: [
      { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
      { to: "/agents", label: "Agents", icon: Bot },
      { to: "/leads", label: "Leads", icon: Users },
      { to: "/calls", label: "Calls", icon: PhoneCall },
      { to: "/handoffs", label: "Handoffs", icon: CalendarClock },
      { to: "/campaigns", label: "Campaigns", icon: Megaphone },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { to: "/analytics", label: "Analytics", icon: BarChart3 },
      { to: "/compliance", label: "Compliance & Audit", icon: ShieldCheck },
    ],
  },
  {
    label: "Configure",
    items: [{ to: "/settings", label: "Settings", icon: Settings }],
  },
];

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="Primary" className="flex-1 space-y-6 overflow-y-auto px-3 py-4">
      {NAV_GROUPS.map((group) => (
        <div key={group.label}>
          <p className="px-3 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
            {group.label}
          </p>
          <ul className="space-y-0.5">
            {group.items.map((item) => {
              const Icon = item.icon;
              return (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.end}
                    onClick={onNavigate}
                    className={({ isActive }) =>
                      cn(
                        "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition",
                        isActive
                          ? "bg-white/10 text-white"
                          : "text-slate-300 hover:bg-white/5 hover:text-white",
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <Icon
                          className={cn(
                            "h-4 w-4 shrink-0",
                            isActive ? "text-accent-300" : "text-slate-400 group-hover:text-slate-200",
                          )}
                          aria-hidden
                        />
                        {item.label}
                      </>
                    )}
                  </NavLink>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

export function SidebarBrand() {
  return (
    <div className="flex h-16 items-center gap-2.5 border-b border-white/10 px-5">
      <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-500 text-sm font-bold text-white">
        C
      </span>
      <div className="leading-tight">
        <p className="text-sm font-semibold text-white">Callora</p>
        <p className="text-[11px] text-slate-400">Voice sales intelligence</p>
      </div>
    </div>
  );
}

export function Sidebar() {
  return (
    <aside className="hidden w-64 shrink-0 flex-col bg-navy-900 lg:flex">
      <SidebarBrand />
      <NavItems />
      <div className="border-t border-white/10 p-3.5 space-y-2">
        <Link
          to="/home"
          className="flex items-center justify-between rounded-lg bg-white/5 px-2.5 py-1.5 text-xs text-slate-300 hover:bg-white/10 hover:text-white transition"
        >
          <span className="flex items-center gap-2">
            <Globe2 className="h-3.5 w-3.5 text-accent-400" /> Product Overview
          </span>
          <ExternalLink className="h-3 w-3 text-slate-500" />
        </Link>
        <div className="flex items-center gap-2 text-[11px] text-slate-400 px-1">
          <Building2 className="h-3.5 w-3.5" aria-hidden />
          <span>Indian outbound operations</span>
        </div>
      </div>
    </aside>
  );
}

export function MobileSidebar({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 lg:hidden">
      <button
        type="button"
        className="absolute inset-0 bg-slate-900/50"
        aria-label="Close navigation"
        onClick={onClose}
      />
      <aside className="relative flex h-full w-72 max-w-[85vw] flex-col bg-navy-900 shadow-pop animate-fade-in">
        <div className="flex items-center justify-between border-b border-white/10 pr-2">
          <SidebarBrand />
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-2 text-slate-300 hover:bg-white/10 hover:text-white"
            aria-label="Close navigation"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <NavItems onNavigate={onClose} />
        <div className="border-t border-white/10 p-4 text-xs text-slate-400">
          <button
            type="button"
            onClick={onClose}
            className="flex items-center gap-1.5 hover:text-white"
          >
            <ChevronLeft className="h-3.5 w-3.5" /> Close menu
          </button>
        </div>
      </aside>
    </div>
  );
}

/** Small helper used by the top bar to highlight the active section. */
export function useActiveNavLabel() {
  const location = useLocation();
  const all = NAV_GROUPS.flatMap((group) => group.items);
  const match = all
    .filter((item) =>
      item.end ? location.pathname === item.to : location.pathname.startsWith(item.to),
    )
    .sort((a, b) => b.to.length - a.to.length)[0];
  return match?.label ?? "Workspace";
}

export { Megaphone };
