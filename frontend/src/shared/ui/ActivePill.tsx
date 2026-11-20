import { motion } from "motion/react";
import { useLayoutEffect, useRef, useState } from "react";

interface PillBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

const GLIDE = { type: "spring", stiffness: 420, damping: 36 } as const;

/** The active highlight of a pill strip: one element that glides between its parent's [data-tab] children.
 *  Measured in the strip's own content coordinates, so the strip scrolling never shifts where it starts.
 *  The strip is positioned and isolated; this goes first in it, under the labels. */
export function ActivePill({ activeIndex }: { activeIndex: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [box, setBox] = useState<PillBox | null>(null);
  // the first placement and a resize land in place; only a change of tab glides
  const glide = useRef(false);

  useLayoutEffect(() => {
    const strip = ref.current?.parentElement;
    if (!strip) return;
    const tabs = () => strip.querySelectorAll<HTMLElement>(":scope > [data-tab]");
    const measure = () => {
      const tab = tabs()[activeIndex];
      setBox(tab ? { x: tab.offsetLeft, y: tab.offsetTop, width: tab.offsetWidth, height: tab.offsetHeight } : null);
    };
    measure();
    // labels change with the language, flex tabs with the strip's width
    const observer = new ResizeObserver(() => {
      glide.current = false;
      measure();
    });
    observer.observe(strip);
    for (const tab of tabs()) observer.observe(tab);
    return () => {
      observer.disconnect();
      glide.current = true;
    };
  }, [activeIndex]);

  return (
    <motion.span
      ref={ref}
      initial={false}
      animate={box ? { x: box.x, y: box.y, width: box.width, height: box.height, opacity: 1 } : { opacity: 0 }}
      transition={glide.current ? GLIDE : { duration: 0 }}
      className="absolute left-0 top-0 rounded-full bg-accent"
    />
  );
}
