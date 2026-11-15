import { useContext, useMemo } from "react";

import type { AppearanceSettings } from "@entities/appearance/api";
import { useShaderMuted } from "@shared/lib/shader-mute";
import type { ShaderColors } from "@shared/lib/shader-palette";
import type { AsyncState } from "@shared/lib/use-async-resource";

import { AppearanceContext, AppearancePreviewContext, AppearanceReloadContext, type AppearancePreview } from "./context";

const DEFAULT_BRAND_NAME = "Remnashop";

export function useAppearance(): AsyncState<AppearanceSettings> {
  const state = useContext(AppearanceContext);
  if (state === null) {
    throw new Error("useAppearance must be used within an AppearanceProvider");
  }
  return state;
}

export function useReloadAppearance(): () => void {
  const reload = useContext(AppearanceReloadContext);
  if (reload === null) {
    throw new Error("useReloadAppearance must be used within an AppearanceProvider");
  }
  return reload;
}

export function useBrandName(): string {
  const state = useAppearance();
  return state.kind === "ready" ? (state.data.brand_name ?? DEFAULT_BRAND_NAME) : DEFAULT_BRAND_NAME;
}

export function useAppearancePreview(): AppearancePreview {
  const value = useContext(AppearancePreviewContext);
  if (value === null) {
    throw new Error("useAppearancePreview must be used within an AppearanceProvider");
  }
  return value;
}

// The live preview if any, otherwise the saved value: what anything painting the accent reads.
export function useEffectiveAccentColor(): string | null {
  const state = useAppearance();
  const { previewColor } = useAppearancePreview();
  return previewColor ?? (state.kind === "ready" ? state.data.primary_color : null);
}

// the live preview if any, otherwise the saved colors; nothing saved follows the accent
export function useEffectiveShaderColors(): ShaderColors {
  const state = useAppearance();
  const { previewShaderColors } = useAppearancePreview();
  const saved = state.kind === "ready" ? state.data : null;
  const base = saved?.shader_base ?? null;
  // by value, so effects keyed on the result don't rerun every render
  const tonesKey = JSON.stringify(saved?.shader_tones ?? null);
  const savedColors = useMemo<ShaderColors>(() => ({ base, tones: JSON.parse(tonesKey) }), [base, tonesKey]);
  return previewShaderColors ?? savedColors;
}

// on while loading, as on the backend; the per-viewer mute wins over both
export function useEffectiveShaderEnabled(): boolean {
  const state = useAppearance();
  const { previewShaderEnabled } = useAppearancePreview();
  const muted = useShaderMuted();
  return !muted && (previewShaderEnabled ?? (state.kind === "ready" ? state.data.shader_enabled : true));
}
