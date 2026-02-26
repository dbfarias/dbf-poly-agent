import { clsx } from "clsx";
import type { ReactNode } from "react";

interface StatCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon?: ReactNode;
  trend?: "up" | "down" | "neutral";
}

export default function StatCard({ title, value, subtitle, icon, trend }: StatCardProps) {
  return (
    <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm text-zinc-400">{title}</span>
        {icon && <span className="text-zinc-500">{icon}</span>}
      </div>
      <div
        className={clsx("text-2xl font-bold", {
          "text-green-400": trend === "up",
          "text-red-400": trend === "down",
          "text-white": trend === "neutral" || !trend,
        })}
      >
        {value}
      </div>
      {subtitle && <div className="text-xs text-zinc-500 mt-1">{subtitle}</div>}
    </div>
  );
}
