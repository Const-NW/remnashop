import { CalendarDays, Coins, Gauge, Gift, Globe, Hash, Network, Package, RefreshCw, Smartphone, Tag, Type, Wallet } from "lucide-react";
import { useTranslation } from "react-i18next";

import { formatLimit, formatTraffic, getSubscription } from "@entities/subscriptions/api";
import type { CreditSegment, SubscriptionListItem } from "@entities/subscriptions/api";
import { usePermissions } from "@entities/user/use-current-user";
import { SubscriptionField } from "@features/subscription-edit/SubscriptionField";
import { useSquadName } from "@features/subscription-edit/use-squad-name";
import { formatDurationLong, isUnlimitedExpiry } from "@shared/lib/duration";
import { formatMoney } from "@shared/lib/format";
import { formatDate } from "@shared/lib/relative-time";
import { useAsyncResource } from "@shared/lib/use-async-resource";
import { DialogCard } from "@shared/ui/glass/DialogCard";

interface SubscriptionPlanTabProps {
  subscription: SubscriptionListItem;
}

export function SubscriptionPlanTab({ subscription }: SubscriptionPlanTabProps) {
  const { t, i18n } = useTranslation();
  const squadName = useSquadName();
  const snapshot = subscription.plan_snapshot;
  const none = t("common.none");
  const unlimited = t("subscriptions.unlimited");
  // a free grant, trial or import stores a hardcoded 0 USD: no price, not a price of zero dollars
  const money = (amount: string) =>
    Number(amount) > 0 ? formatMoney(amount, snapshot.currency, i18n.language, true) : none;

  return (
    <div className="flex flex-col gap-3">
      <DialogCard title={t("subscriptions.plan.title")} icon={Package}>
        <SubscriptionField icon={Type} label={t("subscriptions.plan.name")} value={snapshot.name} />
        <SubscriptionField icon={Wallet} label={t("subscriptions.plan.price")} value={money(snapshot.price_paid)} />
        <SubscriptionField
          icon={CalendarDays}
          label={t("subscriptions.plan.duration")}
          value={
            // an import from the panel stores 0 with no term known: "forever" only when it is
            snapshot.duration_days === 0 && !isUnlimitedExpiry(subscription.expire_at)
              ? none
              : formatDurationLong(snapshot.duration_days, t)
          }
        />
        <SubscriptionField icon={Hash} label={t("subscriptions.plan.code")} value={snapshot.public_code} mono />
        <SubscriptionField
          icon={Gift}
          label={t("subscriptions.plan.trial")}
          value={snapshot.is_trial ? t("common.yes") : t("common.no")}
        />
        <SubscriptionField
          icon={Gauge}
          label={t("subscriptions.fields.traffic")}
          value={formatTraffic(snapshot.traffic_limit, unlimited)}
        />
        <SubscriptionField
          icon={Smartphone}
          label={t("subscriptions.fields.devices")}
          value={formatLimit(snapshot.device_limit, unlimited)}
        />
        <SubscriptionField
          icon={RefreshCw}
          label={t("subscriptions.edit.strategyLabel")}
          value={t(`subscriptions.edit.strategy.${snapshot.traffic_limit_strategy}`)}
        />
        <SubscriptionField icon={Tag} label={t("subscriptions.edit.tagLabel")} value={snapshot.tag} />
        <SubscriptionField
          icon={Network}
          label={t("subscriptions.details.internalSquads")}
          value={snapshot.internal_squads.map(squadName).join(", ")}
          copyValue={snapshot.internal_squads.join(", ")}
        />
        <SubscriptionField
          icon={Globe}
          label={t("subscriptions.details.externalSquad")}
          value={snapshot.external_squad && squadName(snapshot.external_squad)}
          copyValue={snapshot.external_squad}
        />
      </DialogCard>
      <CreditSegmentsCard subscriptionId={subscription.id} />
    </div>
  );
}

function CreditSegmentsCard({ subscriptionId }: { subscriptionId: number }) {
  const { t, i18n } = useTranslation();
  const canView = usePermissions().includes("SUBSCRIPTIONS_DETAIL_VIEW");
  const [details] = useAsyncResource(
    (signal) => (canView ? getSubscription(subscriptionId, signal) : Promise.resolve(null)),
    { deps: [canView, subscriptionId] },
  );
  const segments = details.kind === "ready" ? (details.data?.credit_segments ?? []) : [];
  if (segments.length === 0) return null;

  const period = (segment: CreditSegment) => {
    if (segment.ends_at === null) return t("subscriptions.credit.unlimited");
    const start = new Date(new Date(segment.ends_at).getTime() - segment.seconds * 1000).toISOString();
    return `${formatDate(start, i18n.language)} — ${formatDate(segment.ends_at, i18n.language)}`;
  };

  return (
    <DialogCard title={t("subscriptions.credit.title")} icon={Coins}>
      {segments.map((segment) => (
        <SubscriptionField
          key={segment.id}
          icon={Coins}
          label={t(`subscriptions.credit.kind.${segment.kind}`)}
          value={[
            formatMoney(segment.paid, segment.currency, i18n.language, true),
            period(segment),
            segment.superseded_by_id !== null ? t("subscriptions.credit.superseded") : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        />
      ))}
    </DialogCard>
  );
}
