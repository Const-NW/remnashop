import { Globe, Palette, RotateCcw, Upload, X } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useMemo, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import {
  type AppearanceImageKind,
  type AppearanceSettings,
  type AppearanceTarget,
  appearanceImageUrl,
  deleteAppearanceImage,
  getAppearance,
  resetAppearanceSettings,
  updateAppearance,
  uploadAppearanceImage,
} from "@entities/appearance/api";
import { getErrorMessage } from "@shared/api/http";
import {
  DEFAULT_BRAND_NAME,
  useAppearancePreview,
  useBrandName,
  useReloadAppearance,
} from "@entities/appearance/use-appearance";
import { DEFAULT_ACCENT_COLOR, HEX_COLOR_PATTERN } from "@shared/lib/color";
import { DEFAULT_SHADER_BASE, resolveShaderStops, SHADER_TONE_COUNT } from "@shared/lib/shader-palette";
import { useAsyncResource } from "@shared/lib/use-async-resource";
import { usePermissions } from "@entities/user/use-current-user";
import { toast } from "@shared/lib/toast";
import { BrandLogo } from "@shared/ui/BrandLogo";
import { ColorPickerField } from "@shared/ui/ColorPickerField";
import { ConfirmDialog } from "@shared/ui/ConfirmDialog";
import { ErrorState } from "@shared/ui/glass/EmptyState";
import { Field } from "@shared/ui/glass/Field";
import { Input } from "@shared/ui/glass/Input";
import { Section } from "@shared/ui/glass/Section";
import { Switch } from "@shared/ui/glass/Switch";
import { Spinner } from "@shared/ui/Spinner";
import { Button } from "@shared/ui/Button";
import { GLASS_INNER_CARD_CLASS } from "@shared/ui/classes";
import { IconButton } from "@shared/ui/IconButton";
import { Pressable } from "@shared/ui/Pressable";
import { SaveFooter } from "@shared/ui/SaveFooter";

const SVG_MIME_TYPE = "image/svg+xml";
const ACCEPTED_FILE_TYPES = `image/jpeg,image/png,image/webp,${SVG_MIME_TYPE}`;

// mirrors the backend's _LIMITS_BY_KIND
const IMAGE_LIMITS: Record<AppearanceImageKind, { minSide: number; maxSide: number; maxKb: number }> = {
  logo: { minSide: 128, maxSide: 1024, maxKb: 1024 },
  favicon: { minSide: 32, maxSide: 512, maxKb: 256 },
  "app-icon": { minSide: 512, maxSide: 1024, maxKb: 1024 },
};

const IMAGE_FLAGS: Record<AppearanceImageKind, "has_logo" | "has_favicon" | "has_app_icon"> = {
  logo: "has_logo",
  favicon: "has_favicon",
  "app-icon": "has_app_icon",
};

const ACCENT_COLOR_PRESETS: readonly string[] = [
  "#22d3ee",
  "#3b82f6",
  "#6366f1",
  "#a855f7",
  "#ec4899",
  "#ef4444",
  "#f97316",
  "#f59e0b",
  "#22c55e",
  "#14b8a6",
];

const NO_PINNED_TONES: readonly null[] = Array(SHADER_TONE_COUNT).fill(null);

interface ShaderSwatchProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  onReset: () => void;
  resetDisabled: boolean;
}

function ShaderSwatch({ label, value, onChange, onReset, resetDisabled }: ShaderSwatchProps) {
  return (
    <div className="flex items-center gap-2 rounded-xl bg-white/[0.03] px-2.5 py-2">
      <ColorPickerField value={value} onChange={onChange} />
      <div className="flex min-w-0 flex-col">
        <span className="truncate text-xs font-medium text-foreground">{label}</span>
        <span className="font-mono text-xs text-muted-foreground">{value}</span>
      </div>
      <IconButton size="7" className="ml-auto" onClick={onReset} disabled={resetDisabled}>
        <RotateCcw className="size-3.5" />
      </IconButton>
    </div>
  );
}

interface ImageSlotProps {
  target: AppearanceTarget;
  kind: AppearanceImageKind;
  hasImage: boolean;
  label: string;
  hint: string;
  canUpload: boolean;
  canDelete: boolean;
  onChanged: (hasImage: boolean) => void;
}

function ImageSlot({ target, kind, hasImage, label, hint, canUpload, canDelete, onChanged }: ImageSlotProps) {
  const { t } = useTranslation();
  const [isBusy, setIsBusy] = useState(false);

  const handleFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;

    const limits = IMAGE_LIMITS[kind];
    if (file.size > limits.maxKb * 1024) {
      toast.error(t("settings.appearance.imageTooLarge", { maxKb: limits.maxKb }));
      return;
    }

    setIsBusy(true);
    try {
      // Vector: no pixel size to check here; the server validates markup and aspect ratio.
      const bitmap = file.type === SVG_MIME_TYPE ? undefined : await createImageBitmap(file).catch(() => null);
      if (bitmap === null) {
        toast.error(t("settings.appearance.imageUnreadable"));
        return;
      }
      if (bitmap) {
        const { width, height } = bitmap;
        bitmap.close();
        if (width !== height) {
          toast.error(t("settings.appearance.imageNotSquare"));
          return;
        }
        if (width < limits.minSide || width > limits.maxSide) {
          toast.error(t("settings.appearance.imageSideOutOfRange", { min: limits.minSide, max: limits.maxSide }));
          return;
        }
      }
      await uploadAppearanceImage(target, kind, file);
      onChanged(true);
    } catch (err) {
      toast.error(getErrorMessage(err, t("settings.appearance.uploadError")));
    } finally {
      setIsBusy(false);
    }
  };

  const handleDelete = async () => {
    setIsBusy(true);
    try {
      await deleteAppearanceImage(target, kind);
      onChanged(false);
    } catch (err) {
      toast.error(getErrorMessage(err, t("settings.appearance.deleteError")));
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <Field label={label} hint={hint}>
      <div className="flex flex-col gap-2">
        <div
          className={`relative flex aspect-square items-center justify-center overflow-hidden rounded-3xl ${
            hasImage ? "glass-well" : "border border-dashed border-white/[0.18] bg-black/[0.24]"
          }`}
        >
          {hasImage ? (
            <img src={appearanceImageUrl(target, kind)} alt="" className="h-full w-full object-contain p-11" />
          ) : (
            <BrandLogo className="size-1/2 text-primary" />
          )}
          {hasImage && canDelete && (
            <Pressable
              type="button"
              onClick={() => void handleDelete()}
              disabled={isBusy}
              className="absolute right-3.5 top-3.5 flex size-6 items-center justify-center rounded-full bg-black/55 text-white transition-colors hover:bg-black/75 disabled:opacity-60"
            >
              <X className="size-3" />
            </Pressable>
          )}
        </div>
        {canUpload && (
          <motion.label
            data-disabled={isBusy || undefined}
            className="flex h-9 cursor-pointer items-center justify-center gap-1.5 rounded-full border border-glass-border bg-white/[0.03] text-sm font-medium text-foreground transition-colors hover:bg-white/[0.06] data-disabled:cursor-not-allowed data-disabled:opacity-60"
          >
            <input
              type="file"
              accept={ACCEPTED_FILE_TYPES}
              className="hidden"
              disabled={isBusy}
              onChange={(event) => void handleFileChange(event)}
            />
            <Upload className="size-4" />
            {hasImage ? t("settings.appearance.replace") : t("settings.appearance.upload")}
          </motion.label>
        )}
      </div>
    </Field>
  );
}

function InnerCard({ children }: { children: ReactNode }) {
  return <div className={`flex flex-col gap-3 p-3.5 ${GLASS_INNER_CARD_CLASS}`}>{children}</div>;
}

// The cabinet's card has no live preview: it would repaint the dashboard, not the cabinet.
export function AppearanceSettingsForm({ target }: { target: AppearanceTarget }) {
  const { t } = useTranslation();
  const isDashboard = target === "dashboard";
  const reloadAppearance = useReloadAppearance();
  const { setPreviewColor, setPreviewShaderColors, setPreviewShaderEnabled } = useAppearancePreview();
  const permissions = usePermissions();
  // null: nothing stored, the default name applies
  const [brandName, setBrandName] = useState<string | null>(null);
  const dashboardBrandName = useBrandName();
  const fallbackBrandName = isDashboard ? dashboardBrandName : DEFAULT_BRAND_NAME;
  const [accentColor, setAccentColor] = useState(DEFAULT_ACCENT_COLOR);
  // last non-preset pick, kept as an extra swatch after a preset replaces it
  const [previousColor, setPreviousColor] = useState<string | null>(null);
  const [hexInput, setHexInput] = useState(DEFAULT_ACCENT_COLOR);
  const [colorTouched, setColorTouched] = useState(false);
  // off: the background follows the accent
  const [ownShaderBase, setOwnShaderBase] = useState(false);
  const [shaderBase, setShaderBase] = useState(DEFAULT_SHADER_BASE);
  const [shaderTones, setShaderTones] = useState<(string | null)[]>([...NO_PINNED_TONES]);
  const [shaderColorsTouched, setShaderColorsTouched] = useState(false);
  const [shaderEnabled, setShaderEnabled] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [isResetConfirmOpen, setIsResetConfirmOpen] = useState(false);

  // seeded per section, on load or that section's reset only; a save or image
  // change would wipe the other section's unsaved edits
  const seedBranding = (data: AppearanceSettings) => {
    setBrandName(data.brand_name);
  };

  const seedColors = (data: AppearanceSettings) => {
    const loadedColor = data.primary_color ?? DEFAULT_ACCENT_COLOR;
    setAccentColor(loadedColor);
    setPreviousColor(ACCENT_COLOR_PRESETS.includes(loadedColor.toLowerCase()) ? null : loadedColor);
    setColorTouched(false);
    setOwnShaderBase(data.shader_base !== null);
    setShaderBase(data.shader_base ?? DEFAULT_SHADER_BASE);
    setShaderTones(data.shader_tones ?? [...NO_PINNED_TONES]);
    setShaderColorsTouched(false);
    setShaderEnabled(data.shader_enabled);
  };

  const [state, , setData] = useAsyncResource((signal) => getAppearance(target, signal), {
    onSuccess: (data) => {
      seedBranding(data);
      seedColors(data);
    },
  });

  // picker, preset or load changed the color: the hex field follows
  useEffect(() => {
    setHexInput(accentColor);
  }, [accentColor]);

  // live preview across the dashboard until a save or reset clears colorTouched
  useEffect(() => {
    if (isDashboard) setPreviewColor(colorTouched ? accentColor : null);
  }, [isDashboard, accentColor, colorTouched, setPreviewColor]);

  const shaderColors = useMemo(
    () => ({ base: ownShaderBase ? shaderBase : null, tones: shaderTones }),
    [ownShaderBase, shaderBase, shaderTones],
  );

  useEffect(() => {
    if (isDashboard) setPreviewShaderColors(shaderColorsTouched ? shaderColors : null);
  }, [isDashboard, shaderColors, shaderColorsTouched, setPreviewShaderColors]);

  // a boolean has no "unset" state, so no touched gate
  useEffect(() => {
    if (isDashboard) setPreviewShaderEnabled(shaderEnabled);
  }, [isDashboard, shaderEnabled, setPreviewShaderEnabled]);

  // AppearanceProvider outlives this form: leaving unsaved must drop the preview
  useEffect(() => {
    if (!isDashboard) return;
    return () => {
      setPreviewColor(null);
      setPreviewShaderColors(null);
      setPreviewShaderEnabled(null);
    };
  }, [isDashboard, setPreviewColor, setPreviewShaderColors, setPreviewShaderEnabled]);

  const setAccent = (value: string) => {
    if (!ACCENT_COLOR_PRESETS.includes(value.toLowerCase())) {
      setPreviousColor(value);
    }
    setAccentColor(value);
    setColorTouched(true);
  };

  const shaderStops = resolveShaderStops(accentColor, shaderColors);

  const changeShaderColors = (ownBase: boolean, base: string, tones: (string | null)[]) => {
    setOwnShaderBase(ownBase);
    setShaderBase(base);
    setShaderTones(tones);
    setShaderColorsTouched(true);
  };

  const setShaderTone = (index: number, value: string | null) =>
    changeShaderColors(ownShaderBase, shaderBase, shaderTones.map((tone, i) => (i === index ? value : tone)));

  const handleHexInputChange = (event: ChangeEvent<HTMLInputElement>) => {
    const value = event.target.value;
    setHexInput(value);
    if (HEX_COLOR_PATTERN.test(value)) setAccent(value.toLowerCase());
  };

  const shownBrandName = brandName ?? fallbackBrandName;
  const trimmedBrandName = shownBrandName.trim();
  const isBrandNameEmpty = trimmedBrandName === "";
  const isBrandNameChanged =
    state.kind === "ready" && trimmedBrandName !== (state.data.brand_name ?? fallbackBrandName);

  const handleSave = async (event: FormEvent) => {
    event.preventDefault();
    setIsSaving(true);
    try {
      const saved = await updateAppearance(target, {
        brand_name: isBrandNameChanged ? trimmedBrandName : undefined,
        primary_color: colorTouched ? accentColor : undefined,
        shader_base: shaderColorsTouched ? (ownShaderBase ? shaderBase : "") : undefined,
        shader_tones: shaderColorsTouched ? shaderTones : undefined,
        shader_enabled: shaderEnabled,
      });
      seedBranding(saved);
      setData(saved);
      reloadAppearance();
      setColorTouched(false);
      setShaderColorsTouched(false);
      toast.success(t("settings.appearance.saveSuccess"));
    } catch (err) {
      toast.error(getErrorMessage(err, t("settings.appearance.saveError")));
    } finally {
      setIsSaving(false);
    }
  };

  const handleReset = async () => {
    setIsResetting(true);
    try {
      // Each image goes through its own delete endpoint so its permission still applies.
      if (state.kind === "ready" && state.data.has_logo && permissions.includes("APPEARANCE_LOGO_DELETE")) {
        await deleteAppearanceImage(target, "logo");
      }
      if (state.kind === "ready" && state.data.has_favicon && permissions.includes("APPEARANCE_FAVICON_DELETE")) {
        await deleteAppearanceImage(target, "favicon");
      }
      if (state.kind === "ready" && state.data.has_app_icon && permissions.includes("APPEARANCE_APP_ICON_DELETE")) {
        await deleteAppearanceImage(target, "app-icon");
      }
      const data = await resetAppearanceSettings(target);
      seedBranding(data);
      seedColors(data);
      setData(data);
      reloadAppearance();
      toast.success(t("settings.appearance.resetSuccess"));
    } catch (err) {
      toast.error(getErrorMessage(err, t("settings.appearance.resetError")));
    } finally {
      setIsResetting(false);
    }
  };

  const handleImageChanged = (kind: AppearanceImageKind, hasImage: boolean) => {
    if (state.kind !== "ready") return;
    setData({ ...state.data, [IMAGE_FLAGS[kind]]: hasImage });
    reloadAppearance();
  };

  const canManage = permissions.includes("APPEARANCE_SETTINGS_MANAGE");
  const canReset = permissions.includes("APPEARANCE_SETTINGS_RESET");
  const canLogo = permissions.includes("APPEARANCE_LOGO_UPLOAD") || permissions.includes("APPEARANCE_LOGO_DELETE");
  const canFavicon =
    permissions.includes("APPEARANCE_FAVICON_UPLOAD") || permissions.includes("APPEARANCE_FAVICON_DELETE");
  const canAppIcon =
    permissions.includes("APPEARANCE_APP_ICON_UPLOAD") || permissions.includes("APPEARANCE_APP_ICON_DELETE");
  const canImages = canLogo || canFavicon || canAppIcon;

  const dirty =
    state.kind === "ready" &&
    (isBrandNameChanged ||
      colorTouched ||
      shaderColorsTouched ||
      shaderEnabled !== state.data.shader_enabled);

  const resetButton = canReset && (
    <Button variant="glassDanger" isLoading={isResetting} onClick={() => setIsResetConfirmOpen(true)}>
      {t("common.reset")}
    </Button>
  );

  const swatchClass = (active: boolean) =>
    `size-8 rounded-full ${active ? "outline-2 outline-offset-2 outline-muted-foreground" : "ring-1 ring-white/10"}`;

  return (
    <form onSubmit={(event) => void handleSave(event)}>
      <Section
        icon={isDashboard ? Palette : Globe}
        title={t(isDashboard ? "settings.appearance.dashboardTitle" : "settings.appearance.cabinetTitle")}
        description={t(isDashboard ? "settings.appearance.dashboardDescription" : "settings.appearance.cabinetDescription")}
        footer={
          state.kind === "ready" &&
          (canManage ? (
            <SaveFooter dirty={dirty} saving={isSaving} disabled={isBrandNameEmpty} beside={resetButton} />
          ) : (
            resetButton && <div className="mt-4 flex items-center gap-3">{resetButton}</div>
          ))
        }
      >
        {state.kind === "loading" && <Spinner size="card" />}
        {state.kind === "error" && <ErrorState text={t("common.loadError")} />}

        {state.kind === "ready" && (
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
              {(canManage || canImages) && (
                <InnerCard>
                  {canManage && (
                    <Field
                      label={t("settings.appearance.brandNameLabel")}
                      htmlFor={`appearance-${target}-brand-name`}
                      error={isBrandNameEmpty ? t("errors.validation.required") : null}
                    >
                      <Input
                        id={`appearance-${target}-brand-name`}
                        value={shownBrandName}
                        onChange={(event) => setBrandName(event.target.value)}
                        aria-invalid={isBrandNameEmpty}
                        maxLength={64}
                      />
                    </Field>
                  )}
                  {canImages && (
                    <div className="flex flex-wrap gap-4">
                      {canLogo && (
                        <div className="min-w-0 flex-1">
                        <ImageSlot
                          target={target}
                          kind="logo"
                          hasImage={state.data.has_logo}
                          label={t("settings.appearance.logoLabel")}
                          hint={t("settings.appearance.logoHint")}
                          canUpload={permissions.includes("APPEARANCE_LOGO_UPLOAD")}
                          canDelete={permissions.includes("APPEARANCE_LOGO_DELETE")}
                          onChanged={(hasImage) => handleImageChanged("logo", hasImage)}
                        />
                        </div>
                      )}
                      {canFavicon && (
                        <div className="min-w-0 flex-1">
                        <ImageSlot
                          target={target}
                          kind="favicon"
                          hasImage={state.data.has_favicon}
                          label={t("settings.appearance.faviconLabel")}
                          hint={t("settings.appearance.faviconHint")}
                          canUpload={permissions.includes("APPEARANCE_FAVICON_UPLOAD")}
                          canDelete={permissions.includes("APPEARANCE_FAVICON_DELETE")}
                          onChanged={(hasImage) => handleImageChanged("favicon", hasImage)}
                        />
                        </div>
                      )}
                      {canAppIcon && (
                        <div className="min-w-0 flex-1">
                        <ImageSlot
                          target={target}
                          kind="app-icon"
                          hasImage={state.data.has_app_icon}
                          label={t("settings.appearance.appIconLabel")}
                          hint={t("settings.appearance.appIconHint")}
                          canUpload={permissions.includes("APPEARANCE_APP_ICON_UPLOAD")}
                          canDelete={permissions.includes("APPEARANCE_APP_ICON_DELETE")}
                          onChanged={(hasImage) => handleImageChanged("app-icon", hasImage)}
                        />
                        </div>
                      )}
                    </div>
                  )}
                </InnerCard>
              )}

              {canManage && (
                <InnerCard>
                  <Field label={t("settings.appearance.accentColorLabel")} htmlFor={`appearance-${target}-accent-hex`}>
                    <div className="flex items-center gap-3.5 rounded-xl bg-white/[0.03] p-3">
                      <div className="flex items-center gap-2">
                        <ColorPickerField
                          value={accentColor}
                          onChange={setAccent}
                        />
                        <Input
                          id={`appearance-${target}-accent-hex`}
                          value={hexInput}
                          onChange={handleHexInputChange}
                          maxLength={7}
                          spellCheck={false}
                          mono
                          className="w-28"
                        />
                      </div>
                      {previousColor && (
                        <Pressable
                          type="button"
                          onClick={() => setAccent(previousColor)}
                          style={{ backgroundColor: previousColor }}
                          className={`ml-auto ${swatchClass(accentColor.toLowerCase() === previousColor.toLowerCase())}`} />
                      )}
                    </div>
                  </Field>

                  <Field label={t("settings.appearance.presetsLabel")}>
                    <div className="flex flex-wrap items-center gap-2.5">
                      {ACCENT_COLOR_PRESETS.map((preset) => (
                        <Pressable
                          key={preset}
                          type="button"
                          onClick={() => setAccent(preset)}
                          style={{ backgroundColor: preset }}
                          className={swatchClass(accentColor.toLowerCase() === preset)} />
                      ))}
                    </div>
                  </Field>

                  <div className="flex flex-col gap-2.5 border-t border-white/[0.08] pt-4">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-sm font-medium text-foreground">{t("settings.appearance.shaderEnabledLabel")}</span>
                      <Switch
                        checked={shaderEnabled}
                        onChange={setShaderEnabled}
                      />
                    </div>
                    <div
                      className={`flex flex-col gap-2.5 transition-opacity ${shaderEnabled ? "" : "pointer-events-none opacity-40"}`}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-sm text-foreground">{t("settings.appearance.shaderOwnBaseLabel")}</span>
                        <Switch
                          checked={ownShaderBase}
                          // starts from the color on screen, so switching on changes nothing yet
                          onChange={(on) => changeShaderColors(on, on ? shaderStops[0] : shaderBase, shaderTones)}
                        />
                      </div>
                      <div
                        className="h-4 w-full rounded-full ring-1 ring-white/10"
                        style={{ background: `linear-gradient(to right, ${shaderStops.join(", ")})` }}
                      />
                      <div className="grid grid-cols-2 gap-2">
                        <div className={ownShaderBase ? "" : "pointer-events-none opacity-40"}>
                          <ShaderSwatch
                            label={t("settings.appearance.shaderBase")}
                            value={shaderStops[0]}
                            onChange={(value) => changeShaderColors(true, value, shaderTones)}
                            onReset={() => changeShaderColors(false, shaderBase, shaderTones)}
                            resetDisabled={!ownShaderBase}
                          />
                        </div>
                        {shaderTones.map((pinned, index) => (
                          <ShaderSwatch
                            key={index}
                            label={t(pinned ? "settings.appearance.shaderTonePinned" : "settings.appearance.shaderToneAuto", {
                              number: index + 1,
                            })}
                            value={shaderStops[index + 1]}
                            onChange={(value) => setShaderTone(index, value)}
                            onReset={() => setShaderTone(index, null)}
                            resetDisabled={pinned === null}
                          />
                        ))}
                      </div>
                    </div>
                  </div>
                </InnerCard>
              )}
            </div>
        )}

        {isResetConfirmOpen && (
          <ConfirmDialog
            title={t("settings.appearance.resetConfirmTitle")}
            message={t("settings.appearance.resetConfirmMessage")}
            confirmLabel={t("common.reset")}
            cancelLabel={t("common.cancel")}
            danger
            onConfirm={() => {
              setIsResetConfirmOpen(false);
              void handleReset();
            }}
            onCancel={() => setIsResetConfirmOpen(false)}
          />
        )}
      </Section>
    </form>
  );
}
