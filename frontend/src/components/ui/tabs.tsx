import { cn } from "@/lib/utils";

export type TabItem = {
  id: string;
  label: string;
  count?: number;
};

export function Tabs({
  items,
  value,
  onChange,
  className,
}: {
  items: TabItem[];
  value: string;
  onChange: (id: string) => void;
  className?: string;
}) {
  return (
    <div
      role="tablist"
      aria-label="Sections"
      className={cn(
        "flex gap-1 overflow-x-auto border-b border-slate-200 scrollbar-thin",
        className,
      )}
    >
      {items.map((item) => {
        const selected = item.id === value;
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(item.id)}
            className={cn(
              "-mb-px flex items-center gap-2 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition",
              selected
                ? "border-accent-600 text-accent-700"
                : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800",
            )}
          >
            {item.label}
            {item.count !== undefined ? (
              <span
                className={cn(
                  "rounded-full px-1.5 py-0.5 text-[11px] font-semibold",
                  selected ? "bg-accent-50 text-accent-700" : "bg-slate-100 text-slate-600",
                )}
              >
                {item.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
