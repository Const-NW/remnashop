import { useRef, useState } from "react";
import { HexColorPicker } from "react-colorful";

import { HEX_COLOR_PATTERN } from "@shared/lib/color";
import { useClickOutside } from "@shared/lib/use-click-outside";
import { useCloseOnScroll } from "@shared/lib/use-close-on-scroll";
import { Input } from "@shared/ui/glass/Input";
import { PortalPopover } from "@shared/ui/PortalPopover";
import { Ripple } from "@shared/ui/Ripple";

// HexColorPicker (200px), the hex field (32px + 8px gap) and the popover padding
const POPOVER_MAX_HEIGHT = 270;

interface ColorPickerFieldProps {
  value: string;
  onChange: (value: string) => void;
  // what the closed swatch shows when it differs from the edited value (a hue
  // rotation preview); the popover still edits the untouched `value`
  displayValue?: string;
}

export function ColorPickerField({ value, onChange, displayValue }: ColorPickerFieldProps) {
  const [isOpen, setIsOpen] = useState(false);
  // what is being typed; null shows the value itself
  const [hexDraft, setHexDraft] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  useClickOutside([ref, popoverRef], isOpen, () => setIsOpen(false));
  useCloseOnScroll(isOpen, popoverRef, () => setIsOpen(false));

  return (
    <div ref={ref} className="relative flex">
      <button
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        style={{ backgroundColor: displayValue ?? value }}
        className="relative size-9 shrink-0 rounded-full border-2 border-glass-border shadow-inner"
      >
        <Ripple />
      </button>

      <PortalPopover
        glass
        open={isOpen}
        triggerRef={ref}
        popoverRef={popoverRef}
        align="left"
        maxHeight={POPOVER_MAX_HEIGHT}
        initial={{ opacity: 0, scale: 0.96, y: -4 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, y: -4 }}
        transition={{ duration: 0.15, ease: "easeOut" }}
        className="w-fit p-3"
      >
        <HexColorPicker color={value} onChange={onChange} />
        <Input
          size="sm"
          mono
          value={hexDraft ?? value}
          onChange={(event) => {
            const text = event.target.value.trim();
            setHexDraft(text);
            if (HEX_COLOR_PATTERN.test(text)) onChange(text.toLowerCase());
          }}
          onBlur={() => setHexDraft(null)}
          maxLength={7}
          spellCheck={false}
          className="mt-2 w-[200px]"
        />
      </PortalPopover>
    </div>
  );
}
