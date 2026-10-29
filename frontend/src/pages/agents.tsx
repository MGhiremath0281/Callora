import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Bot,
  BookOpen,
  Copy,
  Globe2,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Trash2,
  Wrench,
} from "lucide-react";

import { useAuth, useBusiness } from "@/app/auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ButtonLink } from "@/components/ui/button-link";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { PageHeader } from "@/components/ui/page-header";
import { useToast } from "@/components/ui/toast";
import {
  EmptyState,
  InlineAlert,
  Skeleton,
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
import { LANGUAGE_NAMES, SUPPORTED_LANGUAGES } from "@/lib/constants";
import { formatDateTime, truncate } from "@/lib/utils";
import {
  getCustomFunctions,
  listKnowledge,
  updateBusiness,
} from "@/services/callora";
import { getCampaigns } from "@/services/campaigns";

type CustomAgentDraft = {
  id: string;
  name: string;
  description: string;
  languages: string[];
  status: "active" | "draft";
  updated: string;
  agentId: string;
  voice: string;
  isPrimary?: boolean;
};

export default function AgentsPage() {
  const business = useBusiness();
  const { refreshBusiness } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [search, setSearch] = useState("");
  const [activationOpen, setActivationOpen] = useState(false);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [agentToDelete, setAgentToDelete] = useState<CustomAgentDraft | null>(null);

  // Form state for creating a new agent
  const [newAgentName, setNewAgentName] = useState("");
  const [newAgentDesc, setNewAgentDesc] = useState("");
  const [newAgentLang, setNewAgentLang] = useState(business.default_language || "en");
  const [newAgentVoice, setNewAgentVoice] = useState("priya_en");

  // Load custom drafts from localStorage
  const storageKey = `callora_extra_agents_${business.business_id}`;
  const [extraAgents, setExtraAgents] = useState<CustomAgentDraft[]>(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      return raw ? JSON.parse(raw) : [];
    } catch {
      return [];
    }
  });

  const saveExtraAgents = (items: CustomAgentDraft[]) => {
    setExtraAgents(items);
    try {
      localStorage.setItem(storageKey, JSON.stringify(items));
    } catch (e) {
      console.error(e);
    }
  };

  const knowledgeQuery = useQuery({
    queryKey: ["knowledge", "summary"],
    queryFn: () => listKnowledge({ limit: 1 }),
  });

  const toolsQuery = useQuery({
    queryKey: ["voice-tools"],
    queryFn: getCustomFunctions,
  });

  const campaignsQuery = useQuery({
    queryKey: ["campaigns", business.business_id],
    queryFn: () => getCampaigns(business.business_id),
  });
  const campaigns = campaignsQuery.data ?? [];

  const activationMutation = useMutation({
    mutationFn: (active: boolean) => updateBusiness({ is_active: active }),
    onSuccess: async (updated) => {
      await refreshBusiness();
      void queryClient.invalidateQueries({ queryKey: ["business"] });
      setActivationOpen(false);
      toast.success(
        updated.is_active ? "Agent activated" : "Agent deactivated",
        updated.is_active
          ? "The configuration is active. Outbound dialling still depends on the deployment's CALLORA_VOICE_ENABLE_OUTBOUND_CALLS setting."
          : "New outbound calls will be refused until the agent is activated again.",
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
    if (!Array.isArray(raw)) return [] as Array<{ name?: string; description?: string }>;
    return raw as Array<{ name?: string; description?: string }>;
  }, [toolsQuery.data]);

  const agents: CustomAgentDraft[] = useMemo(() => {
    const primary: CustomAgentDraft = {
      id: business.business_id,
      name: business.name,
      description: business.agent_instructions || "Workspace primary outbound sales agent",
      languages: business.supported_languages?.length
        ? business.supported_languages
        : [business.default_language],
      status: business.is_active ? "active" : "draft",
      updated: business.updated_at || new Date().toISOString(),
      agentId: business.bolna_agent_id || "Not bound",
      voice: "Priya (Indian English)",
      isPrimary: true,
    };

    return [primary, ...extraAgents];
  }, [business, extraAgents]);

  const filtered = agents.filter((agent) =>
    search.trim()
      ? `${agent.name} ${agent.id} ${agent.agentId || ""}`
          .toLowerCase()
          .includes(search.trim().toLowerCase())
      : true,
  );

  // Handle agent creation
  const handleCreateAgent = () => {
    if (!newAgentName.trim()) return;
    const newAgent: CustomAgentDraft = {
      id: `agt_${Math.random().toString(36).substring(2, 9)}`,
      name: newAgentName.trim(),
      description: newAgentDesc.trim() || "Outbound voice qualification agent",
      languages: [newAgentLang],
      status: "draft",
      updated: new Date().toISOString(),
      agentId: "Draft profile",
      voice: newAgentVoice === "aarav_hi" ? "Aarav (Hindi)" : newAgentVoice === "kavya_kn" ? "Kavya (Kannada)" : "Priya (Indian English)",
      isPrimary: false,
    };

    saveExtraAgents([...extraAgents, newAgent]);
    toast.success("Agent profile created", `Draft agent "${newAgent.name}" saved.`);
    setCreateDialogOpen(false);
    setNewAgentName("");
    setNewAgentDesc("");
  };

  // Handle agent duplication
  const handleDuplicate = (agent: CustomAgentDraft) => {
    const clone: CustomAgentDraft = {
      ...agent,
      id: `agt_${Math.random().toString(36).substring(2, 9)}`,
      name: `Copy of ${agent.name}`,
      status: "draft",
      updated: new Date().toISOString(),
      isPrimary: false,
    };
    saveExtraAgents([...extraAgents, clone]);
    toast.success("Agent duplicated", `Created draft "${clone.name}".`);
  };

  // Handle agent deletion
  const handleDelete = () => {
    if (!agentToDelete || agentToDelete.isPrimary) return;
    saveExtraAgents(extraAgents.filter((a) => a.id !== agentToDelete.id));
    toast.success("Agent removed", `Agent "${agentToDelete.name}" was deleted.`);
    setAgentToDelete(null);
  };

  return (
    <div>
      <PageHeader
        title="Agents"
        description="Configure the voice agents that represent this business: identity, languages, instructions, qualification flow, calling limits and the Bolna agent binding."
        actions={
          <>
            <Button variant="outline" onClick={() => setCreateDialogOpen(true)}>
              <Plus className="h-4 w-4" aria-hidden /> Create agent
            </Button>
            <ButtonLink to={`/agents/${business.business_id}`}>
              <Settings2 className="h-4 w-4" aria-hidden /> Open Agent Studio
            </ButtonLink>
          </>
        }
        meta={
          <>
            <Badge tone={business.is_active ? "success" : "neutral"}>
              {business.is_active ? "Active" : "Draft"}
            </Badge>
            <Badge tone={business.provider_configured ? "success" : "warning"}>
              Provider {business.provider_configured ? "configured" : "missing"}
            </Badge>
            <Badge tone="neutral">{business.business_id}</Badge>
          </>
        }
      />

      <div className="space-y-4">
        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <SummaryTile
            icon={Bot}
            label="Total Agents"
            value={String(agents.length)}
            hint={`${agents.filter((a) => a.status === "active").length} active · ${agents.filter((a) => a.status === "draft").length} draft`}
          />
          <SummaryTile
            icon={BookOpen}
            label="Knowledge items"
            value={
              knowledgeQuery.isPending ? <Skeleton className="h-6 w-8" /> : String(knowledgeQuery.data?.total ?? 0)
            }
            hint="Used for live follow-up answers"
          />
          <SummaryTile
            icon={Wrench}
            label="Voice tools"
            value={
              toolsQuery.isPending ? <Skeleton className="h-6 w-8" /> : String(tools.length)
            }
            hint="Bolna custom functions exposed"
          />
          <SummaryTile
            icon={Globe2}
            label="Languages"
            value={String(business.supported_languages?.length || 1)}
            hint={business.supported_languages
              .map((code) => LANGUAGE_NAMES[code] || code)
              .join(", ")}
          />
        </section>

        <Card>
          <CardHeader
            title="Workspace agents"
            description="Search and manage voice agent configurations available to this workspace."
            actions={
              <div className="relative">
                <Search
                  className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400"
                  aria-hidden
                />
                <Input
                  aria-label="Search agents"
                  className="h-8 w-48 pl-8 text-xs"
                  placeholder="Search agents"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </div>
            }
          />
          {filtered.length === 0 ? (
            <EmptyState
              title="No agent matches"
              description="Clear the search to see configured agents."
            />
          ) : (
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Agent</TH>
                    <TH>Languages</TH>
                    <TH>Voice / Model</TH>
                    <TH>Campaigns</TH>
                    <TH>Status</TH>
                    <TH>Updated</TH>
                    <TH className="text-right">Actions</TH>
                  </TR>
                </THead>
                <TBody>
                  {filtered.map((agent) => {
                    const assignedCampaigns = campaigns.filter(
                      (c) => c.agent_id === agent.id || (agent.isPrimary && c.agent_id === business.business_id),
                    );

                    return (
                      <TR key={agent.id}>
                        <TD>
                          <div className="flex items-center gap-3">
                            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-50 text-accent-600">
                              <Bot className="h-4 w-4" aria-hidden />
                            </span>
                            <div className="min-w-0">
                              <Link
                                to={`/agents/${agent.id}`}
                                className="block truncate font-medium text-slate-900 hover:text-accent-700"
                              >
                                {agent.name}
                              </Link>
                              <p className="truncate text-xs text-slate-500">
                                {truncate(agent.description, 70) ||
                                  "No instructions saved yet."}
                              </p>
                              <p className="font-mono text-[11px] text-slate-400">
                                {agent.id} {agent.isPrimary && "· Primary"}
                              </p>
                            </div>
                          </div>
                        </TD>
                        <TD>
                          <div className="flex flex-wrap gap-1">
                            {agent.languages.map((code) => (
                              <Badge key={code} tone="neutral">
                                {LANGUAGE_NAMES[code] || code}
                              </Badge>
                            ))}
                          </div>
                        </TD>
                        <TD>
                          <p className="max-w-[180px] truncate text-xs font-medium text-slate-700">
                            {agent.voice || "Priya (Indian English)"}
                          </p>
                          <p className="font-mono text-[11px] text-slate-400">
                            {agent.agentId || "Bolna engine"}
                          </p>
                        </TD>
                        <TD>
                          <span className="text-xs font-semibold text-slate-700">
                            {assignedCampaigns.length > 0 ? (
                              <Badge tone="accent">{assignedCampaigns.length} campaigns</Badge>
                            ) : (
                              <span className="text-slate-400">0</span>
                            )}
                          </span>
                        </TD>
                        <TD>
                          <Badge tone={agent.status === "active" ? "success" : "neutral"}>
                            {agent.status}
                          </Badge>
                        </TD>
                        <TD className="whitespace-nowrap text-xs text-slate-500">
                          {formatDateTime(agent.updated)}
                        </TD>
                        <TD>
                          <div className="flex items-center justify-end gap-1">
                            <ButtonLink
                              to={`/agents/${agent.id}`}
                              variant="outline"
                              size="sm"
                            >
                              Edit
                            </ButtonLink>
                            {agent.isPrimary ? (
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => setActivationOpen(true)}
                              >
                                {business.is_active ? "Disable" : "Activate"}
                              </Button>
                            ) : null}
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => handleDuplicate(agent)}
                              title="Duplicate agent"
                              aria-label="Duplicate agent"
                            >
                              <Copy className="h-3.5 w-3.5" />
                            </Button>
                            {!agent.isPrimary && (
                              <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => setAgentToDelete(agent)}
                                title="Delete agent"
                                aria-label="Delete agent"
                              >
                                <Trash2 className="h-3.5 w-3.5 text-rose-500" />
                              </Button>
                            )}
                          </div>
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </Card>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader
              title="What Agent Studio controls"
              description="The real fields stored by the backend."
            />
            <CardBody className="grid gap-2 text-sm text-slate-600 sm:grid-cols-2">
              {[
                "Agent identity and instructions",
                "Hindi, Kannada and English",
                "Primary language and switching hints",
                "Qualification questions",
                "Calling window and days",
                "Retry and quota limits",
                "Human transfer rules",
                "Bolna agent + tool binding",
              ].map((item) => (
                <p key={item} className="flex items-start gap-2">
                  <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" aria-hidden />
                  {item}
                </p>
              ))}
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Voice tools"
              description="Custom functions the Bolna agent can call mid-conversation."
              actions={
                <Badge tone={toolsQuery.data ? "success" : "neutral"}>
                  {tools.length} available
                </Badge>
              }
            />
            <CardBody>
              {toolsQuery.isPending ? (
                <Skeleton className="h-20 w-full" />
              ) : toolsQuery.error ? (
                <p className="text-sm text-slate-500">
                  Tool definitions could not be loaded from the backend.
                </p>
              ) : tools.length === 0 ? (
                <p className="text-sm text-slate-500">
                  No custom functions are exposed by this deployment.
                </p>
              ) : (
                <ul className="space-y-2">
                  {tools.slice(0, 6).map((tool) => (
                    <li
                      key={tool.name}
                      className="flex items-center justify-between gap-3 text-sm"
                    >
                      <span className="font-mono text-xs text-slate-700">{tool.name}</span>
                      <span className="truncate text-xs text-slate-400">
                        {truncate(tool.description, 48)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <ButtonLink
                to={`/agents/${business.business_id}?tab=advanced`}
                variant="outline"
                size="sm"
                className="mt-4"
              >
                View tool configuration
              </ButtonLink>
            </CardBody>
          </Card>
        </div>
      </div>

      {/* Create Agent Modal */}
      {createDialogOpen && (
        <Dialog open={createDialogOpen} onClose={() => setCreateDialogOpen(false)} title="Create Voice Agent" size="md">
          <div className="p-2">
            <p className="mb-4 text-xs text-slate-500">
              Define a new outbound qualification agent profile.
            </p>

            <div className="space-y-4">
              <Field label="Agent Name" required>
                <Input
                  placeholder="e.g. Kannada Logistics Qualifier"
                  value={newAgentName}
                  onChange={(e) => setNewAgentName(e.target.value)}
                  autoFocus
                />
              </Field>
              <Field label="Sales Objective / Description">
                <Textarea
                  rows={3}
                  placeholder="e.g. Call retail partners to book enterprise demo"
                  value={newAgentDesc}
                  onChange={(e) => setNewAgentDesc(e.target.value)}
                />
              </Field>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Primary Language">
                  <Select value={newAgentLang} onChange={(e) => setNewAgentLang(e.target.value)}>
                    <option value="en">English (India)</option>
                    <option value="hi">Hindi</option>
                    <option value="kn">Kannada</option>
                  </Select>
                </Field>
                <Field label="Voice Model">
                  <Select value={newAgentVoice} onChange={(e) => setNewAgentVoice(e.target.value)}>
                    <option value="priya_en">Priya (Indian English)</option>
                    <option value="aarav_hi">Aarav (Hindi)</option>
                    <option value="kavya_kn">Kavya (Kannada)</option>
                  </Select>
                </Field>
              </div>
            </div>

            <div className="mt-6 flex justify-end gap-2 border-t border-slate-100 pt-4">
              <Button variant="ghost" onClick={() => setCreateDialogOpen(false)}>
                Cancel
              </Button>
              <Button onClick={handleCreateAgent} disabled={!newAgentName.trim()}>
                Create Draft
              </Button>
            </div>
          </div>
        </Dialog>
      )}

      {/* Delete Agent Confirmation */}
      {agentToDelete && (
        <ConfirmDialog
          open={Boolean(agentToDelete)}
          onClose={() => setAgentToDelete(null)}
          title="Delete agent draft"
          description={`Are you sure you want to delete the draft agent "${agentToDelete.name}"?`}
          confirmLabel="Delete Agent"
          tone="danger"
          onConfirm={handleDelete}
        />
      )}

      <ConfirmDialog
        open={activationOpen}
        onClose={() => setActivationOpen(false)}
        title={business.is_active ? "Deactivate the sales agent?" : "Activate the sales agent?"}
        description={
          business.is_active
            ? "When deactivated, the compliance gate refuses every outbound call for this business, even if triggered via the API."
            : "Activating the agent allows outbound calls that pass consent, DND, calling window and quota checks. Outbound calling still requires CALLORA_VOICE_ENABLE_OUTBOUND_CALLS on the server."
        }
        confirmLabel={business.is_active ? "Deactivate agent" : "Activate agent"}
        tone={business.is_active ? "danger" : "primary"}
        loading={activationMutation.isPending}
        onConfirm={() => activationMutation.mutate(!business.is_active)}
      />
    </div>
  );
}

function SummaryTile({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: typeof Bot;
  label: string;
  value: ReactNode;
  hint: string;
}) {
  return (
    <Card className="p-3.5">
      <div className="flex items-center gap-2 text-slate-500">
        <Icon className="h-4 w-4" aria-hidden />
        <p className="text-xs font-medium text-slate-600">{label}</p>
      </div>
      <p className="mt-2 text-xl font-bold tracking-tight text-slate-900">{value}</p>
      <p className="mt-0.5 truncate text-[11px] text-slate-500">{hint}</p>
    </Card>
  );
}
