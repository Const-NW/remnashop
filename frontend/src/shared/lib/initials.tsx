import { User } from "lucide-react";
import type { ReactNode } from "react";

/** "Anna Ivanova" -> "AI", "anna" -> "AN"; a user icon when there is nothing to take them from. Letters and digits only: a symbol the font may lack would render as a box. */
export function initials(text: string | null | undefined): ReactNode {
  const words = (text ?? "")
    .trim()
    .split(/\s+/)
    .map((word) => Array.from(word).filter((char) => /[\p{L}\p{N}]/u.test(char)))
    .filter((chars) => chars.length > 0);
  if (words.length === 0) return <User className="size-4" />;
  const letters = words.length > 1 ? words[0][0] + words[1][0] : words[0].slice(0, 2).join("");
  return letters.toUpperCase();
}
