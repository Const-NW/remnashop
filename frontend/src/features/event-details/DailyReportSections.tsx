import {
  ArrowRightLeft,
  BadgeCheck,
  ChartColumn,
  CircleDollarSign,
  CreditCard,
  FlaskConical,
  Gem,
  Handshake,
  RefreshCw,
  ShoppingCart,
  Unplug,
  UserPlus,
  Users,
  Wallet,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import type { EventLogEntry } from "@entities/event-log/api";
import { nameOrUnnamed } from "@entities/user/display";
import { formatMoney } from "@shared/lib/format";
import { InfoRow } from "@shared/ui/InfoRow";
import { DialogCard } from "@shared/ui/glass/DialogCard";

/** `DailyReportEvent`'s payload as the outbox stores it. */
interface DailyReportPayload {
  new_users: number;
  new_trials: number;
  trial_conversions: number;
  new_payers: number;
  currency: string;
  revenue: string;
  active_trials: number;
  active_paid: number;
  paid_never_connected: number;
  purchases: number;
  purchases_amount: string;
  renewals: number;
  renewals_amount: string;
  changes: number;
  changes_amount: string;
  /** (user id, name, invited) */
  top_referrers: [number, string, number][];
}

export function DailyReportSections({ entry }: { entry: EventLogEntry }) {
  const { t, i18n } = useTranslation();
  const report = entry.payload as unknown as DailyReportPayload;
  const count = (value: number) => value.toLocaleString(i18n.language);
  const money = (amount: string) => formatMoney(amount, report.currency, i18n.language);
  const paid = (value: number, amount: string) => `${count(value)} · ${money(amount)}`;

  return (
    <>
      <DialogCard title={t("eventLog.detail.report.day")} icon={ChartColumn}>
        <InfoRow icon={UserPlus} label={t("eventLog.detail.report.newUsers")}>
          {count(report.new_users)}
        </InfoRow>
        <InfoRow icon={FlaskConical} label={t("eventLog.detail.report.newTrials")}>
          {count(report.new_trials)}
        </InfoRow>
        <InfoRow icon={BadgeCheck} label={t("eventLog.detail.report.trialConversions")}>
          {count(report.trial_conversions)}
        </InfoRow>
        <InfoRow icon={CreditCard} label={t("eventLog.detail.report.newPayers")}>
          {count(report.new_payers)}
        </InfoRow>
        <InfoRow icon={Wallet} label={t("eventLog.detail.report.revenue")}>
          {money(report.revenue)}
        </InfoRow>
      </DialogCard>

      <DialogCard title={t("eventLog.detail.report.subscriptions")} icon={Gem}>
        <InfoRow icon={FlaskConical} label={t("eventLog.detail.report.activeTrials")}>
          {count(report.active_trials)}
        </InfoRow>
        <InfoRow icon={Users} label={t("eventLog.detail.report.activePaid")}>
          {count(report.active_paid)}
        </InfoRow>
        <InfoRow icon={Unplug} label={t("eventLog.detail.report.paidNeverConnected")}>
          {count(report.paid_never_connected)}
        </InfoRow>
      </DialogCard>

      <DialogCard title={t("eventLog.detail.report.payments")} icon={CircleDollarSign}>
        <InfoRow icon={ShoppingCart} label={t("eventLog.detail.report.purchases")}>
          {paid(report.purchases, report.purchases_amount)}
        </InfoRow>
        <InfoRow icon={RefreshCw} label={t("eventLog.detail.report.renewals")}>
          {paid(report.renewals, report.renewals_amount)}
        </InfoRow>
        <InfoRow icon={ArrowRightLeft} label={t("eventLog.detail.report.changes")}>
          {paid(report.changes, report.changes_amount)}
        </InfoRow>
      </DialogCard>

      {report.top_referrers.length > 0 && (
        <DialogCard title={t("eventLog.detail.report.referrers")} icon={Handshake}>
          {report.top_referrers.map(([userId, name, invited], index) => (
            <InfoRow key={userId} icon={UserPlus} label={`${index + 1}. ${nameOrUnnamed(name, userId, t)}`}>
              {count(invited)}
            </InfoRow>
          ))}
        </DialogCard>
      )}
    </>
  );
}
