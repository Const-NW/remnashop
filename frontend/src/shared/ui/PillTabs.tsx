import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

import { useHorizontalWheelScroll } from "@shared/lib/use-horizontal-wheel-scroll";
import { ActivePill } from "@shared/ui/ActivePill";
import { TAB_HOVER_CLASS } from "@shared/ui/classes";

interface PillTabsProps<T extends string> {
  items: { key: T; label: ReactNode }[];
  activeKey: T | null;
  onSelect: (key: T) => void;
  disabled?: boolean;
  /** false: no backdrop blur, for a capsule that shouldn't frost what's behind it */
  blurred?: boolean;
}

/** The settings TabBar's capsule, for tabs that switch state instead of routes:
 * the editor's tabs, the locale row, the inspector's pair. */
export function PillTabs<T extends string>({
  items,
  activeKey,
  onSelect,
  disabled = false,
  blurred = true,
}: PillTabsProps<T>) {
  const scrollerRef = useHorizontalWheelScroll<HTMLDivElement>();
  const activeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    // keeps the selected tab in view (twenty locales overflow); scrolls the strip only,
    // scrollIntoView would drag the page down to an editor below the fold
    const active = activeRef.current;
    const scroller = active?.parentElement;
    if (!active || !scroller) return;
    const a = active.getBoundingClientRect();
    const s = scroller.getBoundingClientRect();
    if (a.left < s.left) scroller.scrollBy({ left: a.left - s.left, behavior: "smooth" });
    else if (a.right > s.right) scroller.scrollBy({ left: a.right - s.right, behavior: "smooth" });
  }, [activeKey]);

  return (
    <div className={`flex w-full rounded-full border border-glass-border bg-white/[0.03] p-1 ${blurred ? "backdrop-blur-md" : ""}`}>
      <div ref={scrollerRef} className="no-scrollbar relative isolate flex min-w-0 flex-1 gap-1 overflow-x-auto rounded-full">
        <ActivePill activeIndex={items.findIndex((item) => item.key === activeKey)} />
        {items.map((item) => (
          <button
            key={item.key}
            ref={activeKey === item.key ? activeRef : undefined}
            type="button"
            data-tab
            onClick={() => onSelect(item.key)}
            disabled={disabled}
            className="group relative flex-1 shrink-0 cursor-pointer rounded-full disabled:cursor-not-allowed disabled:opacity-50"
          >
            {activeKey !== item.key && <span className={TAB_HOVER_CLASS} />}
            <span
              className={`relative z-10 flex h-[26px] items-center justify-center whitespace-nowrap px-3.5 text-sm font-medium transition-colors ${activeKey === item.key ? "text-primary" : "text-foreground"}`}
            >
              {item.label}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
