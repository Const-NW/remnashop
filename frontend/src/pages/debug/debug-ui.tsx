import { FlaskConical, LayoutGrid } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useMatch } from "react-router-dom";

import { useKit } from "@pages/debug/use-kit";
import { ActivePill } from "@shared/ui/ActivePill";
import { EYEBROW_CLASS, PAGE_BAR_CLASS, PAGE_CLASS, PAGE_SCROLL_UNDER_BAR_CLASS, TAB_HOVER_CLASS } from "@shared/ui/classes";
import { TabBar } from "@shared/ui/glass/TabBar";

/** The debug page frame, as on the settings pages: a bar floating over a scroller of its own, so main's
 *  top fade can't dim the bar. bar: what floats beside the Kit / Variants tabs. */
export function DebugShell({ bar, children }: { bar?: ReactNode; children: ReactNode }) {
  const { t } = useTranslation();
  const isKit = useMatch({ path: "/dashboard/debug", end: true }) !== null;
  return (
    <div data-fills-main className={PAGE_CLASS}>
      <div className={`${PAGE_BAR_CLASS} flex gap-3`}>
        <TabBar
          items={[
            { key: "kit", to: "/dashboard/debug", icon: LayoutGrid, label: t("debugPage.tabs.kit"), isActive: isKit },
            { key: "variants", to: "/dashboard/debug/variants", icon: FlaskConical, label: t("debugPage.tabs.variants"), isActive: !isKit },
          ]}
        />
        {bar && <div className="min-w-0 flex-1">{bar}</div>}
      </div>
      <div className={`${PAGE_SCROLL_UNDER_BAR_CLASS} flex flex-col gap-6`}>
        {children}
      </div>
    </div>
  );
}

// a smooth scroll this far drags on; it jumps instead
const FAR_JUMP_SCREENS = 1.5;
// a click's scroll counts as over once the page has been still this long
const JUMP_SETTLE_MS = 150;
// a section a few px short of its scroll-to spot still counts as reached
const SPY_SLACK_PX = 8;

export interface DebugSectionLink {
  id: string;
  label: string;
}

const pxOf = (value: string) => parseFloat(value) || 0;

/** The last section whose top has reached the spot scrollIntoView puts it at; the last one at the very bottom. */
function sectionAtScroll(scroller: Element, sections: HTMLElement[]): HTMLElement {
  if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - SPY_SLACK_PX) {
    return sections[sections.length - 1];
  }
  const viewTop = scroller === document.scrollingElement ? 0 : scroller.getBoundingClientRect().top;
  const line = viewTop + pxOf(getComputedStyle(scroller).scrollPaddingTop) + SPY_SLACK_PX;
  let current = sections[0];
  for (const section of sections) {
    if (section.getBoundingClientRect().top - pxOf(getComputedStyle(section).scrollMarginTop) <= line) current = section;
  }
  return current;
}

/** Sticky section strip; highlights the section scrolled to. */
export function DebugNav({ links }: { links: DebugSectionLink[] }) {
  const [active, setActive] = useState(links[0]?.id);
  const navRef = useRef<HTMLElement>(null);
  // set while a click's scroll runs: the sections it passes aren't selections
  const jumpTimer = useRef<number | undefined>(undefined);

  const holdUntilSettled = () => {
    window.clearTimeout(jumpTimer.current);
    jumpTimer.current = window.setTimeout(() => (jumpTimer.current = undefined), JUMP_SETTLE_MS);
  };

  const jumpTo = (id: string) => {
    const section = document.getElementById(id);
    if (!section) return;
    setActive(id);
    holdUntilSettled();
    const far = Math.abs(section.getBoundingClientRect().top) > window.innerHeight * FAR_JUMP_SCREENS;
    section.scrollIntoView({ behavior: far ? "instant" : "smooth", block: "start" });
  };

  useEffect(() => {
    const onScroll = (event: Event) => {
      const scroller = event.target instanceof Element ? event.target : document.scrollingElement;
      if (!scroller || scroller === navRef.current) return;
      if (jumpTimer.current !== undefined) {
        holdUntilSettled();
        return;
      }
      const sections = links.flatMap(({ id }) => document.getElementById(id) ?? []);
      if (sections.length === 0 || !scroller.contains(sections[0])) return;
      setActive(sectionAtScroll(scroller, sections).id);
    };
    // capture: scroll doesn't bubble, and the page scrolls in a container of its own
    document.addEventListener("scroll", onScroll, true);
    return () => document.removeEventListener("scroll", onScroll, true);
  }, [links]);

  // keeps the active tab in view; scrolls the strip only
  useEffect(() => {
    const nav = navRef.current;
    const tab = nav?.querySelectorAll<HTMLElement>(":scope > [data-tab]")[links.findIndex((link) => link.id === active)];
    if (!nav || !tab) return;
    nav.scrollTo({ left: tab.offsetLeft - (nav.clientWidth - tab.offsetWidth) / 2, behavior: "smooth" });
  }, [active, links]);

  return (
    // the glass TabBar's capsule: 48px, 36px items 6px in
    // a mouse wheel scrolls it sideways: the strip outgrows the bar and hides its scrollbar
    <nav
      ref={navRef}
      onWheel={(event) => {
        if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) event.currentTarget.scrollLeft += event.deltaY;
      }}
      className="glass-panel no-scrollbar relative isolate flex h-12 gap-1 overflow-x-auto rounded-full p-1.5"
    >
      <ActivePill activeIndex={links.findIndex((link) => link.id === active)} />
      {links.map(({ id, label }) => (
        <button
          key={id}
          type="button"
          data-tab
          onClick={() => jumpTo(id)}
          className="group relative h-9 flex-1 shrink-0 whitespace-nowrap rounded-full text-sm font-medium"
        >
          {active !== id && <span className={TAB_HOVER_CLASS} />}
          <span className={`relative z-10 px-4 transition-colors ${active === id ? "text-primary" : "text-foreground"}`}>{label}</span>
        </button>
      ))}
    </nav>
  );
}

/** A titled group of specimens; two columns from sm up. Title and meta come from debugPage.kit.sections.<id>. */
export function DebugSection({ id, children }: { id: string; children: ReactNode }) {
  const k = useKit();
  return (
    // the scroller's scroll-pt clears the nav on desktop; scroll-mt clears the fixed mobile header
    <section id={id} className="flex max-md:scroll-mt-16 flex-col gap-3.5">
      <div className="flex items-baseline justify-between gap-4 border-b border-white/[0.08] px-1 pb-2.5">
        <h2 className="text-base font-semibold tracking-tight text-foreground">{k(`sections.${id}.title`)}</h2>
        <span className={`truncate ${EYEBROW_CLASS}`}>{k(`sections.${id}.meta`)}</span>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">{children}</div>
    </section>
  );
}

/** One live sample: a stage with the component, then its name and where it is used. */
export function Specimen({
  name,
  usage,
  wide = false,
  stage = "center",
  children,
}: {
  name: string;
  usage: string;
  /** spans both columns */
  wide?: boolean;
  /** center: the sample sits centered; stretch: it fills the stage width (fields, tables) */
  stage?: "center" | "stretch";
  children: ReactNode;
}) {
  return (
    <div
      className={`glass-panel flex flex-col rounded-3xl ${
        wide ? "sm:col-span-2" : ""
      }`}
    >
      <div
        // no overflow-hidden on the card: tooltips and menus inside a stage must not be clipped
        className={`flex min-h-[120px] flex-1 rounded-t-3xl bg-black/[0.18] p-5 ${
          stage === "center" ? "flex-wrap items-center justify-center gap-2" : "flex-col justify-center gap-3"
        }`}
      >
        {children}
      </div>
      <div className="flex flex-col gap-0.5 p-4">
        <span className="font-mono text-xs font-semibold text-foreground">{name}</span>
        <span className="text-xs text-muted-foreground">{usage}</span>
      </div>
    </div>
  );
}
