import { useEffect, useRef, useState, type ComponentType, type MouseEvent } from "react";

import { useHorizontalWheelScroll } from "@shared/lib/use-horizontal-wheel-scroll";
import { ActivePill } from "@shared/ui/ActivePill";
import { MotionLink } from "@shared/ui/MotionLink";
import { TAB_HOVER_CLASS } from "@shared/ui/classes";

export interface TabBarItem {
  key: string;
  to: string;
  icon: ComponentType<{ className?: string }>;
  label: string;
  isActive: boolean;
}

/** Route tabs in a 48px glass-panel capsule: 36px tabs 6px in, 14px medium. */
export function TabBar({ items }: { items: TabBarItem[] }) {
  const tabsRef = useHorizontalWheelScroll<HTMLDivElement>();
  const navRef = useRef<HTMLElement>(null);
  const activeKey = items.find((item) => item.isActive)?.key;
  // the router switches tabs only once the next page has rendered; the pill sets off on the click instead
  const [pressed, setPressed] = useState<{ key: string; from: string | undefined } | null>(null);
  const shownKey = pressed && pressed.from === activeKey ? pressed.key : activeKey;

  // a deep link to a tab past the edge: scroll the strip only, scrollIntoView would move the page
  useEffect(() => {
    const active = navRef.current?.querySelector<HTMLElement>("[data-shown]");
    const container = active?.parentElement;
    if (!container || !active) return;
    container.scrollTo({ left: active.offsetLeft - (container.clientWidth - active.offsetWidth) / 2, behavior: "smooth" });
  }, [shownKey]);

  return (
    <nav ref={navRef} className="glass-panel inline-flex h-12 max-w-full rounded-full p-1.5">
      <div ref={tabsRef} className="no-scrollbar relative isolate flex gap-1 overflow-x-auto rounded-full">
        <ActivePill activeIndex={items.findIndex((item) => item.key === shownKey)} />
        {items.map(({ key, to, icon: Icon, label, isActive }) => (
          <MotionLink
            key={key}
            to={to}
            data-tab
            data-shown={key === shownKey || undefined}
            aria-current={isActive ? "page" : undefined}
            onClick={(event: MouseEvent) => {
              // navigating to the active tab would only re-run navigate()
              if (isActive) event.preventDefault();
              else setPressed({ key, from: activeKey });
            }}
            className="group relative flex h-9 shrink-0 items-center rounded-full text-sm font-medium"
          >
            {key !== shownKey && <span className={TAB_HOVER_CLASS} />}
            <span
              className={`relative z-10 flex items-center gap-1.5 whitespace-nowrap px-4 transition-colors ${
                key === shownKey ? "text-primary" : "text-foreground"
              }`}
            >
              <Icon className="size-4 shrink-0" />
              {label}
            </span>
          </MotionLink>
        ))}
      </div>
    </nav>
  );
}
