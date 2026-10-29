import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { useToast } from "@/components/ui/toast";
import {
  EmptyState,
  ErrorState,
  Skeleton,
  TableSkeleton,
} from "@/components/ui/states";
import { KNOWLEDGE_CATEGORIES } from "@/lib/constants";
import { formatDateTime, truncate } from "@/lib/utils";
import {
  createKnowledge,
  deleteKnowledge,
  listKnowledge,
  updateKnowledge,
} from "@/services/callora";
import type { KnowledgeItem } from "@/types/api";

const knowledgeSchema = z.object({
  category: z.string().min(1, "Choose a category."),
  title: z.string().trim().min(3, "Title must be at least 3 characters.").max(255),
  content: z.string().trim().min(10, "Content must be at least 10 characters."),
  tags: z.string().max(500).optional(),
  source_reference: z.string().max(255).optional(),
  priority: z.coerce.number().int().min(0).max(100),
  is_active: z.boolean(),
});

type KnowledgeForm = z.infer<typeof knowledgeSchema>;

const EMPTY_FORM: KnowledgeForm = {
  category: "faq",
  title: "",
  content: "",
  tags: "",
  source_reference: "Verified Business Knowledge",
  priority: 0,
  is_active: true,
};

export function KnowledgeManager() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [category, setCategory] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<KnowledgeItem | null>(null);
  const [deleting, setDeleting] = useState<KnowledgeItem | null>(null);

  const queryKey = useMemo(() => ["knowledge", category], [category]);
  const knowledgeQuery = useQuery({
    queryKey,
    queryFn: () =>
      listKnowledge({ limit: 200, category: category || undefined }),
  });

  const form = useForm<KnowledgeForm>({
    resolver: zodResolver(knowledgeSchema),
    defaultValues: EMPTY_FORM,
  });

  const saveMutation = useMutation({
    mutationFn: async (values: KnowledgeForm) => {
      const payload = {
        category: values.category,
        title: values.title.trim(),
        content: values.content.trim(),
        tags: values.tags
          ? values.tags
              .split(",")
              .map((tag) => tag.trim())
              .filter(Boolean)
          : [],
        source_reference: values.source_reference?.trim() || "Verified Business Knowledge",
        priority: values.priority,
        is_active: values.is_active,
      };
      if (editing) return updateKnowledge(editing.id, payload);
      return createKnowledge(payload);
    },
    onSuccess: () => {
      toast.success(editing ? "Knowledge item updated" : "Knowledge item created");
      setDialogOpen(false);
      setEditing(null);
      void queryClient.invalidateQueries({ queryKey: ["knowledge"] });
    },
    onError: (error) => {
      toast.error(
        "Knowledge item was not saved",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const toggleMutation = useMutation({
    mutationFn: (item: KnowledgeItem) =>
      updateKnowledge(item.id, { is_active: !item.is_active }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      toast.success("Knowledge status updated");
    },
    onError: (error) => {
      toast.error(
        "Could not change the knowledge status",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (item: KnowledgeItem) => deleteKnowledge(item.id),
    onSuccess: () => {
      toast.success("Knowledge item deleted");
      setDeleting(null);
      void queryClient.invalidateQueries({ queryKey: ["knowledge"] });
    },
    onError: (error) => {
      toast.error(
        "Knowledge item was not deleted",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  function openCreate() {
    setEditing(null);
    form.reset(EMPTY_FORM);
    setDialogOpen(true);
  }

  function openEdit(item: KnowledgeItem) {
    setEditing(item);
    form.reset({
      category: item.category,
      title: item.title,
      content: item.content,
      tags: item.tags.join(", "),
      source_reference: item.source_reference,
      priority: item.priority,
      is_active: item.is_active,
    });
    setDialogOpen(true);
  }

  const items = knowledgeQuery.data?.items ?? [];

  return (
    <div>
      <Card>
        <CardHeader
          title="Knowledge base"
          description="Verified facts the voice agent may quote mid-call. Empty knowledge means the agent falls back to an honest “I don't know”. "
          actions={
            <>
              <Select
                aria-label="Filter by category"
                value={category}
                onChange={(event) => setCategory(event.target.value)}
                className="h-8 w-40 py-1 text-xs"
              >
                <option value="">All categories</option>
                {KNOWLEDGE_CATEGORIES.map((value) => (
                  <option key={value} value={value}>
                    {value.replace(/_/g, " ")}
                  </option>
                ))}
              </Select>
              <Button size="sm" onClick={openCreate}>
                <Plus className="h-3.5 w-3.5" aria-hidden /> Add knowledge
              </Button>
            </>
          }
        />
        {knowledgeQuery.isPending ? (
          <TableSkeleton rows={5} columns={3} />
        ) : knowledgeQuery.error ? (
          <ErrorState
            error={knowledgeQuery.error}
            title="Knowledge base unavailable"
            onRetry={() => void knowledgeQuery.refetch()}
          />
        ) : items.length === 0 ? (
          <EmptyState
            icon={<BookOpen className="h-5 w-5" aria-hidden />}
            title="No verified knowledge yet"
            description="Add pricing, eligibility, policy and objection-handling facts. Without them, follow-up questions are answered conservatively rather than invented."
            action={
              <Button size="sm" onClick={openCreate}>
                Add the first item
              </Button>
            }
          />
        ) : (
          <ul className="divide-y divide-slate-100">
            {items.map((item) => (
              <li key={item.id} className="px-5 py-4">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-sm font-semibold text-slate-900">
                        {item.title}
                      </h3>
                      <Badge tone="accent">{item.category.replace(/_/g, " ")}</Badge>
                      <Badge tone={item.is_active ? "success" : "neutral"}>
                        {item.is_active ? "active" : "inactive"}
                      </Badge>
                      <span className="text-[11px] text-slate-400">
                        priority {item.priority}
                      </span>
                    </div>
                    <p className="mt-1 text-sm leading-relaxed text-slate-600">
                      {truncate(item.content, 220)}
                    </p>
                    <p className="mt-1 text-[11px] text-slate-400">
                      {item.source_reference} · updated {formatDateTime(item.updated_at)}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => toggleMutation.mutate(item)}
                      loading={toggleMutation.isPending && toggleMutation.variables?.id === item.id}
                    >
                      {item.is_active ? "Disable" : "Enable"}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Edit ${item.title}`}
                      onClick={() => openEdit(item)}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete ${item.title}`}
                      onClick={() => setDeleting(item)}
                    >
                      <Trash2 className="h-3.5 w-3.5 text-rose-500" />
                    </Button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
        {items.length > 0 ? (
          <CardBody className="border-t border-slate-100 py-3 text-xs text-slate-500">
            {items.length} item{items.length === 1 ? "" : "s"} shown. Only active items are
            retrieved for live answers.{" "}
            <button
              type="button"
              className="inline-flex items-center gap-1 text-accent-600 hover:text-accent-700"
              onClick={() => void knowledgeQuery.refetch()}
            >
              <RefreshCw className="h-3 w-3" aria-hidden /> Refresh
            </button>
          </CardBody>
        ) : null}
      </Card>

      <Dialog
        open={dialogOpen}
        onClose={() => {
          setDialogOpen(false);
          setEditing(null);
        }}
        title={editing ? "Edit knowledge item" : "Add verified knowledge"}
        description="Only add facts the business can stand behind. The agent quotes this content during live calls."
        size="lg"
        footer={
          <>
            <Button
              variant="outline"
              onClick={() => {
                setDialogOpen(false);
                setEditing(null);
              }}
              disabled={saveMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={form.handleSubmit((values) => saveMutation.mutate(values))}
              loading={saveMutation.isPending}
            >
              {editing ? "Save changes" : "Create item"}
            </Button>
          </>
        }
      >
        <form
          className="space-y-4"
          onSubmit={form.handleSubmit((values) => saveMutation.mutate(values))}
          noValidate
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label="Category"
              htmlFor="knowledge-category"
              error={form.formState.errors.category?.message}
              required
            >
              <Select id="knowledge-category" {...form.register("category")}>
                {KNOWLEDGE_CATEGORIES.map((value) => (
                  <option key={value} value={value}>
                    {value.replace(/_/g, " ")}
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              label="Priority"
              htmlFor="knowledge-priority"
              hint="Higher priority is retrieved first."
              error={form.formState.errors.priority?.message}
            >
              <Input
                id="knowledge-priority"
                type="number"
                min={0}
                max={100}
                {...form.register("priority")}
              />
            </Field>
          </div>

          <Field
            label="Title"
            htmlFor="knowledge-title"
            error={form.formState.errors.title?.message}
            required
          >
            <Input
              id="knowledge-title"
              placeholder="e.g. Standard plan pricing"
              {...form.register("title")}
            />
          </Field>

          <Field
            label="Verified content"
            htmlFor="knowledge-content"
            hint="Write the exact answer the agent may read aloud. Keep it short and factual."
            error={form.formState.errors.content?.message}
            required
          >
            <Textarea
              id="knowledge-content"
              rows={7}
              placeholder="The standard plan is ₹4,999 per month and includes two users."
              {...form.register("content")}
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label="Tags"
              htmlFor="knowledge-tags"
              hint="Comma separated."
              error={form.formState.errors.tags?.message}
            >
              <Input id="knowledge-tags" placeholder="pricing, plans" {...form.register("tags")} />
            </Field>
            <Field
              label="Source reference"
              htmlFor="knowledge-source"
              error={form.formState.errors.source_reference?.message}
            >
              <Input id="knowledge-source" {...form.register("source_reference")} />
            </Field>
          </div>

          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-slate-300 text-accent-600"
              {...form.register("is_active")}
            />
            Available to the live agent immediately
          </label>
        </form>
      </Dialog>

      <ConfirmDialog
        open={Boolean(deleting)}
        onClose={() => setDeleting(null)}
        onConfirm={() => deleting && deleteMutation.mutate(deleting)}
        title="Delete knowledge item?"
        description={
          <>
            <strong>{deleting?.title}</strong> will be removed from the business knowledge
            base and can no longer ground live answers. This cannot be undone.
          </>
        }
        confirmLabel="Delete item"
        tone="danger"
        loading={deleteMutation.isPending}
      />
    </div>
  );
}
