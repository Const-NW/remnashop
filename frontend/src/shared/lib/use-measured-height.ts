import { useEffect, useRef, useState, type RefObject } from "react";

// ResizeObserver-driven so a dialog also eases when a tab's own content changes (sessions loading in)
export function useMeasuredHeight(): [RefObject<HTMLDivElement | null>, number | "auto"] {
  const ref = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number | "auto">("auto");

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    // border box: the measured element's own padding is part of the height to animate to
    const observer = new ResizeObserver(([entry]) => setHeight(entry.borderBoxSize[0].blockSize));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return [ref, height];
}
