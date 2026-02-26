import { clsx } from "clsx";
import type { ReactNode } from "react";

interface StatCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon?: ReactNode;
  trend?: "up" | "down" | "neutral";
  testId?: string;
}

export default function StatCard({ title, value, subtitle, icon, trend, testId }: StatCardProps) {
  return (
    <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4" data-testid={testId ? `stat-card-${testId}` : undefined}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm text-zinc-400" data-testid={testId ? `stat-title-${testId}` : undefined}>{title}</span>
        {icon && <span className="text-zinc-500">{icon}</span>}
      </div>
      <div
        className={clsx("text-2xl font-bold", {
          "text-green-400": trend === "up",
          "text-red-400": trend === "down",
          "text-white": trend === "neutral" || !trend,
        })}
        data-testid={testId ? `stat-value-${testId}` : undefined}
      >
        {value}
      </div>
      {subtitle && <div className="text-xs text-zinc-500 mt-1" data-testid={testId ? `stat-subtitle-${testId}` : undefined}>{subtitle}</div>}
    </div>
  );
}
