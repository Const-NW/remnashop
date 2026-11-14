import { useRef } from "react";

import { PREMIUM_EMOJI_RE } from "@entities/dialogs/api";
import { EmojiPicker } from "@entities/emoji-packs/EmojiPicker";
import type { PickedEmoji } from "@entities/emoji-packs/EmojiPicker";
import { RichTextField } from "@features/dialog-canvas-editor/RichTextField";
import type { RichTextFieldHandle } from "@features/dialog-canvas-editor/RichTextField";

interface PlanDescriptionFieldProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled: boolean;
}

/** Plain text with premium emoji, drawn as images; the value keeps their `[glyph](emoji:id)` markup. */
export function PlanDescriptionField({ value, onChange, placeholder, disabled }: PlanDescriptionFieldProps) {
  const fieldRef = useRef<RichTextFieldHandle>(null);
  const pickEmoji = (picked: PickedEmoji) =>
    fieldRef.current?.insert("glyph" in picked ? picked.glyph : `[${picked.fallback}](emoji:${picked.customEmojiId})`);

  return (
    <div
      className={`glass-well relative flex items-start rounded-[18px] pr-2 ring-inset focus-within:ring-1 focus-within:ring-primary ${
        disabled ? "cursor-not-allowed" : "cursor-text"
      }`}
    >
      <div className="relative min-w-0 flex-1">
        {value === "" && placeholder && (
          <span className="pointer-events-none absolute inset-x-3.5 top-3 truncate text-sm leading-5 text-muted-foreground/70">
            {placeholder.replace(PREMIUM_EMOJI_RE, "$1")}
          </span>
        )}
        <RichTextField
          ref={fieldRef}
          value={value}
          disabled={disabled}
          onChange={onChange}
          className="min-h-[4.25rem] w-full px-3.5 py-3 text-sm leading-5 text-foreground outline-none"
        />
      </div>
      <div className="pt-2">
        <EmojiPicker current={null} disabled={disabled} onPick={pickEmoji} onClear={() => undefined} />
      </div>
    </div>
  );
}
