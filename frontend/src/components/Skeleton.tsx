import { clsx } from "clsx";

interface SkeletonProps {
  className?: string;
  style?: React.CSSProperties;
}

/** Animated shimmer placeholder for loading states. */
export function Skeleton({ className, style }: SkeletonProps) {
  return (
    <div
      className={clsx(
        "animate-pulse rounded bg-zinc-700/40",
        className,
      )}
      style={style}
      data-testid="skeleton"
    />
  );
}

/** Skeleton replacement for a StatCard while loading. */
export function StatCardSkeleton() {
  return (
    <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4" data-testid="stat-card-skeleton">
      <div className="flex items-center justify-between mb-2">
        <Skeleton className="h-4 w-20" />
        <Skeleton className="h-4 w-4 rounded" />
      </div>
      <Skeleton className="h-8 w-24 mt-1" />
      <Skeleton className="h-3 w-16 mt-2" />
    </div>
  );
}

/** Skeleton replacement for a chart card while loading. */
export function ChartSkeleton({ title }: { title?: string }) {
  return (
    <div
      className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4 h-64"
      data-testid="chart-skeleton"
    >
      {title && (
        <div className="text-sm font-medium text-zinc-400 mb-4">{title}</div>
      )}
      <div className="flex items-end gap-1 h-[200px] pt-4">
        {Array.from({ length: 12 }).map((_, i) => (
          <Skeleton
            key={i}
            className="flex-1"
            style={{
              height: `${30 + Math.sin(i * 0.8) * 25 + Math.random() * 20}%`,
            }}
          />
        ))}
      </div>
    </div>
  );
}
