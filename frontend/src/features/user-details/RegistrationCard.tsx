import {
  CornerDownRight,
  Globe,
  Languages,
  Link2,
  LogIn,
  Megaphone,
  Monitor,
  MousePointerClick,
  PanelTop,
  Smartphone,
  SquareArrowRightEnter,
  Star,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { getUserRegistration } from "@entities/user/api";
import { useAsyncResource } from "@shared/lib/use-async-resource";
import { DialogCard } from "@shared/ui/glass/DialogCard";
import { InfoRow } from "@shared/ui/InfoRow";

import type { RegistrationSnapshot } from "@entities/user/api";

const UTM_KEYS = [
  "utm_source",
  "utm_medium",
  "utm_campaign",
  "utm_content",
  "utm_term",
] as const;

export function RegistrationCard({ userId }: { userId: number }) {
  const { t } = useTranslation();
  const [state] = useAsyncResource(
    (signal) => getUserRegistration(userId, signal),
    { deps: [userId] },
  );
  // accounts older than registration tracking have no record: no card at all
  if (state.kind !== "ready" || state.data.registration === null) return null;
  const registration = state.data.registration;
  const utm = utmSummary(registration);
  return (
    <DialogCard collapsible title={t("users.registration.title")} icon={LogIn}>
      <InfoRow
        icon={SquareArrowRightEnter}
        label={t("users.registration.source")}
      >
        {t(`users.registration.sources.${registration.source}`)}
      </InfoRow>
      {registration.start_payload && (
        <InfoRow
          icon={Link2}
          label={t("users.registration.startPayload")}
          copyValue={registration.start_payload}
          mono
        >
          <span className="truncate">{registration.start_payload}</span>
        </InfoRow>
      )}
      {registration.telegram_language_code && (
        <InfoRow
          icon={Languages}
          label={t("users.registration.telegramLanguage")}
          mono
        >
          {registration.telegram_language_code}
        </InfoRow>
      )}
      {registration.telegram_is_premium !== null && (
        <InfoRow icon={Star} label={t("users.registration.telegramPremium")}>
          {registration.telegram_is_premium ? t("common.yes") : t("common.no")}
        </InfoRow>
      )}
      {registration.ip_address && (
        <InfoRow
          icon={Globe}
          label={t("users.registration.ip")}
          copyValue={registration.ip_address}
          mono
        >
          {registration.ip_address}
        </InfoRow>
      )}
      {registration.browser && (
        <InfoRow
          icon={Monitor}
          label={t("users.registration.browser")}
          copyValue={registration.user_agent ?? undefined}
        >
          <span className="truncate">{registration.browser}</span>
        </InfoRow>
      )}
      {registration.platform && (
        <InfoRow
          icon={Smartphone}
          label={t("users.registration.platform")}
          mono
        >
          {registration.platform}
        </InfoRow>
      )}
      {/* URLs come from the customer's browser: shown as text, never as links */}
      {registration.landing_url && (
        <InfoRow
          icon={PanelTop}
          label={t("users.registration.landingUrl")}
          copyValue={registration.landing_url}
          mono
        >
          <span className="truncate">{registration.landing_url}</span>
        </InfoRow>
      )}
      {registration.referer && (
        <InfoRow
          icon={CornerDownRight}
          label={t("users.registration.referer")}
          copyValue={registration.referer}
          mono
        >
          <span className="truncate">{registration.referer}</span>
        </InfoRow>
      )}
      {utm && (
        <InfoRow
          icon={Megaphone}
          label={t("users.registration.utm")}
          copyValue={utm.full}
          mono
        >
          <span className="truncate">{utm.short}</span>
        </InfoRow>
      )}
      {registration.click_id && (
        <InfoRow
          icon={MousePointerClick}
          label={t("users.registration.clickId")}
          copyValue={registration.click_id}
          mono
        >
          <span className="truncate">{registration.click_id}</span>
        </InfoRow>
      )}
    </DialogCard>
  );
}

function utmSummary(
  registration: RegistrationSnapshot,
): { short: string; full: string } | null {
  const present = UTM_KEYS.filter((key) => registration[key]);
  if (present.length === 0) return null;
  const short = [
    registration.utm_source,
    registration.utm_medium,
    registration.utm_campaign,
  ]
    .filter(Boolean)
    .join(" / ");
  const full = present.map((key) => `${key}=${registration[key]}`).join("&");
  return { short: short || full, full };
}
