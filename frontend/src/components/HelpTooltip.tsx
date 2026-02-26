import { HelpCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

interface HelpTooltipProps {
  text: string;
  size?: number;
}

export default function HelpTooltip({ text, size = 13 }: HelpTooltipProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent | TouchEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("touchstart", handler);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("touchstart", handler);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative inline-flex items-center">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        className="text-zinc-500 hover:text-zinc-300 transition-colors ml-1 focus:outline-none"
        aria-label="Help"
        data-testid="help-tooltip-trigger"
      >
        <HelpCircle size={size} />
      </button>
      {open && (
        <div
          className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 w-56 sm:w-64 px-3 py-2 rounded-lg bg-[#2a2d3e] border border-[#3a3d4e] text-xs text-zinc-300 leading-relaxed shadow-lg"
          data-testid="help-tooltip-content"
        >
          {text}
          <div className="absolute top-full left-1/2 -translate-x-1/2 -mt-px">
            <div className="w-2 h-2 rotate-45 bg-[#2a2d3e] border-r border-b border-[#3a3d4e]" />
          </div>
        </div>
      )}
    </div>
  );
}
