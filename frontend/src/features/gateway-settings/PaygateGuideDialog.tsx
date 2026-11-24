import { useTranslation } from "react-i18next";

import { withEmphasis, withLink } from "@shared/lib/text-emphasis";
import { GuideCopy, GuideDialog, GuideHeading, GuideTip } from "@shared/ui/glass/GuideDialog";

const ENV_NAMES = [
  "YOOKASSA_SHOP_ID",
  "YOOKASSA_SECRET_KEY",
  "INGRESS_SECRET",
  "FORWARD_SECRET",
  "INGRESS_ALLOWED_IPS",
  "PUBLIC_URL",
  "FORWARD_URL",
  "ALLOWED_RETURN_ORIGINS",
  "RETURN_MODE=buttons",
  "RETURN_DIRECT_ORIGINS",
  "WEBHOOK_PATH",
  "https://",
];
const PAYGATE_REPO_URL = "https://github.com/remnashop/paygate_yookassa";
const EVENTS = ["payment.succeeded", "payment.canceled", "refund.succeeded"];

/** Setup of the paygate relay; `webhookUrl` is where paygate forwards notifications. */
export function PaygateGuideDialog({ webhookUrl }: { webhookUrl: string | null }) {
  const { t } = useTranslation();
  const relaySteps = t("settings.gateways.paygateGuide.relaySteps", { returnObjects: true }) as string[];
  const dashboardSteps = t("settings.gateways.paygateGuide.dashboardSteps", { returnObjects: true }) as string[];
  // test payments and a cabinet checkout without its own page return to the app's domain
  const appOrigin = webhookUrl ? new URL(webhookUrl).origin : null;

  return (
    <GuideDialog title={t("settings.gateways.paygateGuide.title")}>
      <p>{withLink(t("settings.gateways.paygateGuide.intro"), "paygate", PAYGATE_REPO_URL)}</p>

      <div>
        <GuideHeading>{t("settings.gateways.paygateGuide.relayTitle")}</GuideHeading>
        <ol className="mt-2 flex list-decimal flex-col gap-1.5 pl-5">
          {relaySteps.map((step, index) => (
            <li key={index}>{withEmphasis(step, ENV_NAMES)}</li>
          ))}
        </ol>
        <div className="mt-3 flex flex-col gap-2">
          {webhookUrl !== null && <GuideCopy label="FORWARD_URL" value={webhookUrl} />}
          <GuideCopy
            label="ALLOWED_RETURN_ORIGINS"
            value={appOrigin ? `https://t.me,${appOrigin}` : "https://t.me"}
          />
        </div>
        <GuideTip className="mt-3">{t("settings.gateways.paygateGuide.originsTip")}</GuideTip>
      </div>

      <div>
        <GuideHeading>{t("settings.gateways.paygateGuide.yookassaTitle")}</GuideHeading>
        <p className="mt-2">{withEmphasis(t("settings.gateways.paygateGuide.yookassaBody"), [...ENV_NAMES, ...EVENTS])}</p>
      </div>

      <div>
        <GuideHeading>{t("settings.gateways.paygateGuide.dashboardTitle")}</GuideHeading>
        <ol className="mt-2 flex list-decimal flex-col gap-1.5 pl-5">
          {dashboardSteps.map((step, index) => (
            <li key={index}>{withEmphasis(step, ENV_NAMES)}</li>
          ))}
        </ol>
      </div>

      <div>
        <GuideHeading>{t("settings.gateways.paygateGuide.returnTitle")}</GuideHeading>
        <p className="mt-2">{withEmphasis(t("settings.gateways.paygateGuide.returnBody"), ENV_NAMES)}</p>
      </div>
    </GuideDialog>
  );
}
