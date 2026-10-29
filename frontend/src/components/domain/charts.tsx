import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { EmptyState } from "@/components/ui/states";

const PALETTE = [
  "#7c5cff",
  "#38bdf8",
  "#34d399",
  "#fbbf24",
  "#fb7185",
  "#94a3b8",
  "#a78bfa",
  "#f97316",
];

const TOOLTIP_STYLE = {
  borderRadius: 10,
  border: "1px solid #e2e8f0",
  fontSize: 12,
  boxShadow: "0 8px 24px rgba(15, 23, 42, 0.1)",
};

export type SeriesPoint = { label: string; value: number };

export function VolumeBarChart({
  data,
  height = 220,
}: {
  data: SeriesPoint[];
  height?: number;
}) {
  if (data.every((point) => point.value === 0)) {
    return (
      <EmptyState
        title="No calls in this window"
        description="Call volume will appear once calls are placed for the selected period."
        className="py-8"
      />
    );
  }

  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#eef2f7" />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 11, fill: "#64748b" }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            allowDecimals={false}
            tick={{ fontSize: 11, fill: "#94a3b8" }}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#f8fafc" }} />
          <Bar dataKey="value" name="Calls" fill="#7c5cff" radius={[4, 4, 0, 0]} maxBarSize={36} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function OutcomePieChart({
  data,
  height = 220,
}: {
  data: SeriesPoint[];
  height?: number;
}) {
  if (data.length === 0) {
    return (
      <EmptyState
        title="No outcomes yet"
        description="Outcome distribution will appear once calls complete."
        className="py-8"
      />
    );
  }

  return (
    <div style={{ height }} className="flex w-full items-center">
      <ResponsiveContainer width="55%" height="100%">
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="label"
            innerRadius="55%"
            outerRadius="82%"
            paddingAngle={2}
            stroke="#fff"
            strokeWidth={2}
          >
            {data.map((point, index) => (
              <Cell key={point.label} fill={PALETTE[index % PALETTE.length]} />
            ))}
          </Pie>
          <Tooltip contentStyle={TOOLTIP_STYLE} />
        </PieChart>
      </ResponsiveContainer>
      <ul className="min-w-0 flex-1 space-y-1.5 text-xs">
        {data.map((point, index) => (
          <li key={point.label} className="flex items-center gap-2">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: PALETTE[index % PALETTE.length] }}
            />
            <span className="min-w-0 flex-1 truncate capitalize text-slate-600">
              {point.label.replace(/_/g, " ")}
            </span>
            <span className="shrink-0 font-semibold tabular-nums text-slate-800">
              {point.value}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function HorizontalBars({
  data,
  color = "#7c5cff",
}: {
  data: SeriesPoint[];
  color?: string;
}) {
  const max = Math.max(1, ...data.map((point) => point.value));
  return (
    <div className="space-y-2.5">
      {data.map((point) => (
        <div key={point.label}>
          <div className="mb-1 flex items-center justify-between gap-3 text-xs">
            <span className="truncate capitalize text-slate-600">
              {point.label.replace(/_/g, " ")}
            </span>
            <span className="shrink-0 font-semibold tabular-nums text-slate-800">
              {point.value}
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-slate-100">
            <div
              className="h-full rounded-full transition-all"
              style={{ width: `${(point.value / max) * 100}%`, backgroundColor: color }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
