import { Bot, Check, Globe } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { getPlanShareUrls } from "@entities/plans/api";
import { copyToClipboard } from "@shared/lib/clipboard";
import { useAsyncResource } from "@shared/lib/use-async-resource";
import { IconButton } from "@shared/ui/IconButton";
import { Tooltip } from "@shared/ui/Tooltip";

function CopyLinkButton({ url, label, icon: Icon }: { url: string; label: string; icon: LucideIcon }) {
  const [isCopied, setIsCopied] = useState(false);
  const copy = () => {
    void copyToClipboard(url).then((copied) => {
      if (!copied) return;
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 1500);
    });
  };
  return (
    <IconButton size="9" tone="glass" className="group relative" onClick={copy}>
      {isCopied ? <Check className="size-4 text-primary" /> : <Icon className="size-4" />}
      {/* below: above it the dialog body's scroll edge clips the bubble */}
      <Tooltip text={label} side="bottom-end" />
    </IconButton>
  );
}

export function PlanShareLinks({ planId }: { planId: number }) {
  const { t } = useTranslation();
  const [state] = useAsyncResource((signal) => getPlanShareUrls(planId, signal), { deps: [planId] });
  if (state.kind !== "ready") return null;
  const { bot_url: botUrl, webcabinet_url: cabinetUrl } = state.data;
  return (
    <>
      {botUrl && <CopyLinkButton url={botUrl} label={t("plans.edit.botLinkLabel")} icon={Bot} />}
      {cabinetUrl && <CopyLinkButton url={cabinetUrl} label={t("plans.edit.cabinetLinkLabel")} icon={Globe} />}
    </>
  );
}
