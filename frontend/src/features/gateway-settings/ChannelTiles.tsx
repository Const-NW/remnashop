import { Check, Globe } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { Gateway, GatewayChannels } from "@entities/gateways/api";
import { TelegramIcon } from "@shared/ui/BrandIcons";
import { Field } from "@shared/ui/glass/Field";
import { IconCircle } from "@shared/ui/glass/IconCircle";
import { Pressable } from "@shared/ui/Pressable";

const CHANNELS = [
  { channel: "telegram", key: "telegram_enabled", icon: TelegramIcon },
  { channel: "cabinet", key: "cabinet_enabled", icon: Globe },
] as const;

/** Where the gateway is offered: two tiles that switch independently. Shaped like a small settings Row: 48px,
 *  the 32px circle 8px in, the corner concentric with it (24px), the 20px check as far from the side as from the edges. */
export function ChannelTiles({
  gateway,
  onChange,
  disabled,
  sharedShop,
}: {
  gateway: Gateway;
  onChange: (channels: GatewayChannels) => void;
  disabled: boolean;
  /** both channels pay into one shop the gateway could split by channel */
  sharedShop: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Field label={t("settings.gateways.channels.title")}>
      <div className="grid gap-2 sm:grid-cols-2">
        {CHANNELS.map(({ channel, key, icon: Icon }) => {
          const on = gateway[key];
          return (
            <Pressable
              key={key}
              type="button"
              disabled={disabled}
              onClick={() => onChange({ [key]: !on })}
              className={`flex min-h-12 items-center gap-2.5 rounded-[24px] border py-1 pl-2 pr-3.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                on ? "border-primary/35 bg-primary/10" : "border-glass-border bg-white/[0.03] enabled:hover:bg-white/[0.06]"
              }`}
            >
              <IconCircle icon={Icon} tone={on ? "accent" : "neutral"} />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium leading-tight text-foreground">{t(`settings.gateways.channels.${channel}`)}</span>
                <span className="block text-xs leading-tight text-muted-foreground">{t(`settings.gateways.channels.${channel}Hint`)}</span>
              </span>
              <span
                className={`flex size-5 shrink-0 items-center justify-center rounded-full border ${
                  on ? "border-primary bg-primary text-primary-foreground" : "border-glass-border"
                }`}
              >
                {on && <Check className="size-3" strokeWidth={3.5} />}
              </span>
            </Pressable>
          );
        })}
      </div>
      <TelegramWarning gateway={gateway} />
      {sharedShop && <p className="text-xs text-warning">{t("settings.gateways.channels.sharedShopWarning")}</p>}
    </Field>
  );
}

function TelegramWarning({ gateway }: { gateway: Gateway }) {
  const { t } = useTranslation();
  if (!gateway.telegram_enabled || gateway.type === "TELEGRAM_STARS") return null;
  return <p className="text-xs text-warning">{t("settings.gateways.channels.warning")}</p>;
}
