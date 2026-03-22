import { useCallback, useEffect, useRef, useState } from "react";

interface UsePullToRefreshOptions {
  onRefresh: () => Promise<void>;
  threshold?: number;
  maxPull?: number;
}

export function usePullToRefresh({
  onRefresh,
  threshold = 60,
  maxPull = 120,
}: UsePullToRefreshOptions) {
  const containerRef = useRef<HTMLElement>(null);
  const [pullDistance, setPullDistance] = useState(0);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const startY = useRef(0);
  const startX = useRef(0);
  const isPulling = useRef(false);
  const isHorizontal = useRef(false);

  const handleTouchStart = useCallback(
    (e: TouchEvent) => {
      if (isRefreshing || !containerRef.current) return;
      // Only activate when scrolled to top
      if (containerRef.current.scrollTop > 0) return;
      startY.current = e.touches[0].clientY;
      startX.current = e.touches[0].clientX;
      isPulling.current = false;
      isHorizontal.current = false;
    },
    [isRefreshing],
  );

  const handleTouchMove = useCallback(
    (e: TouchEvent) => {
      if (isRefreshing || !containerRef.current) return;
      if (containerRef.current.scrollTop > 0) return;

      const deltaY = e.touches[0].clientY - startY.current;
      const deltaX = Math.abs(e.touches[0].clientX - startX.current);

      // Detect horizontal scroll (chart interactions) — ignore
      if (!isPulling.current && !isHorizontal.current) {
        if (deltaX > Math.abs(deltaY)) {
          isHorizontal.current = true;
          return;
        }
      }
      if (isHorizontal.current) return;

      if (deltaY > 0) {
        isPulling.current = true;
        // Diminishing returns — pull feels natural
        const distance = Math.min(deltaY * 0.5, maxPull);
        setPullDistance(distance);
        e.preventDefault();
      }
    },
    [isRefreshing, maxPull],
  );

  const handleTouchEnd = useCallback(async () => {
    if (!isPulling.current) return;
    isPulling.current = false;

    if (pullDistance >= threshold && !isRefreshing) {
      setIsRefreshing(true);
      setPullDistance(0);
      try {
        await onRefresh();
      } finally {
        setIsRefreshing(false);
      }
    } else {
      setPullDistance(0);
    }
  }, [pullDistance, threshold, isRefreshing, onRefresh]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    el.addEventListener("touchstart", handleTouchStart, { passive: true });
    el.addEventListener("touchmove", handleTouchMove, { passive: false });
    el.addEventListener("touchend", handleTouchEnd, { passive: true });

    return () => {
      el.removeEventListener("touchstart", handleTouchStart);
      el.removeEventListener("touchmove", handleTouchMove);
      el.removeEventListener("touchend", handleTouchEnd);
    };
  }, [handleTouchStart, handleTouchMove, handleTouchEnd]);

  return { pullDistance, isRefreshing, containerRef };
}
