import { AppWindow, CalendarPlus, Fingerprint, Globe, Monitor, RefreshCw, Smartphone, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  deleteSubscriptionDevice,
  listSubscriptionDevices,
} from "@entities/subscriptions/api";
import { useAccountPermissions } from "@entities/user/use-current-user";
import { getErrorMessage } from "@shared/api/http";
import { formatDate, formatDateTime, formatRelative } from "@shared/lib/relative-time";
import { toast } from "@shared/lib/toast";
import { useAsyncResource } from "@shared/lib/use-async-resource";
import { DialogCard } from "@shared/ui/glass/DialogCard";
import { EmptyState, ErrorState } from "@shared/ui/glass/EmptyState";
import { Row } from "@shared/ui/glass/Row";
import { IconButton } from "@shared/ui/IconButton";
import { InfoRow } from "@shared/ui/InfoRow";
import { Spinner } from "@shared/ui/Spinner";

// the panel's clock may run ahead of the browser's, and "in 2 minutes" reads wrong here
function pastOrNow(iso: string): string {
  return new Date(Math.min(Date.parse(iso), Date.now())).toISOString();
}

export function SubscriptionDevicesTab({ subscriptionId, userId }: { subscriptionId: number; userId: number }) {
  const { t, i18n } = useTranslation();
  const permissions = useAccountPermissions(userId);
  const canDelete = permissions.includes("SUBSCRIPTIONS_DEVICES_DELETE");
  const [state, reload, setData] = useAsyncResource((signal) => listSubscriptionDevices(subscriptionId, signal));
  const [isBusy, setIsBusy] = useState(false);
  const [openHwid, setOpenHwid] = useState<string | null>(null);

  const run = async (action: () => Promise<void>, done: string) => {
    setIsBusy(true);
    try {
      await action();
      toast.success(done);
      reload();
    } catch (err) {
      toast.error(getErrorMessage(err, t("subscriptions.devices.error")));
    } finally {
      setIsBusy(false);
    }
  };

  // the row goes at once, so it can't be clicked again while the list reloads
  const deleteDevice = async (hwid: string) => {
    await deleteSubscriptionDevice(subscriptionId, hwid);
    setData((prev) => ({
      items: prev.items.filter((device) => device.hwid !== hwid),
      total: prev.total - 1,
    }));
  };

  return (
    <div className="flex flex-col gap-3">
      <DialogCard>
        {state.kind === "loading" && <Spinner size="card" />}
        {state.kind === "error" && <ErrorState text={t("common.loadError")} />}
        {state.kind === "ready" && state.data.items.length === 0 && (
          <EmptyState icon={Smartphone} text={t("subscriptions.devices.empty")} />
        )}
        {state.kind === "ready" &&
          state.data.items.map((device) => (
            <Row
              key={device.hwid}
              size="sm"
              icon={Smartphone}
              label={
                [device.name, device.device_model, [device.platform, device.os_version].filter(Boolean).join(" "), device.app]
                  .filter(Boolean)
                  .join(" · ") || t("common.none")
              }
              description={t("subscriptions.devices.added", { date: formatDate(device.created_at, i18n.language) })}
              expanded={openHwid === device.hwid}
              // a narrow dialog needs that width for the name
              chevron={false}
              onClick={() => setOpenHwid((open) => (open === device.hwid ? null : device.hwid))}
              panel={
                <div className="flex flex-col">
                  <InfoRow icon={Fingerprint} label={t("subscriptions.devices.details.hwid")} copyValue={device.hwid} mono>
                    <span className="truncate">{device.hwid}</span>
                  </InfoRow>
                  {device.app && (
                    <InfoRow icon={AppWindow} label={t("subscriptions.devices.details.app")}>
                      {device.app}
                    </InfoRow>
                  )}
                  {device.platform && (
                    <InfoRow icon={Monitor} label={t("subscriptions.devices.details.platform")}>
                      {[device.platform, device.os_version].filter(Boolean).join(" ")}
                    </InfoRow>
                  )}
                  {device.device_model && (
                    <InfoRow icon={Smartphone} label={t("subscriptions.devices.details.model")}>
                      {device.device_model}
                    </InfoRow>
                  )}
                  {device.user_agent && (
                    <InfoRow icon={Globe} label={t("subscriptions.devices.details.userAgent")} copyValue={device.user_agent} mono>
                      <span className="truncate">{device.user_agent}</span>
                    </InfoRow>
                  )}
                  <InfoRow icon={CalendarPlus} label={t("subscriptions.devices.details.added")} mono>
                    <span className="whitespace-nowrap">{formatDateTime(device.created_at, i18n.language)}</span>
                  </InfoRow>
                  <InfoRow icon={RefreshCw} label={t("subscriptions.devices.details.lastFetched")} mono>
                    <span className="whitespace-nowrap">{formatDateTime(device.last_fetched_at, i18n.language)}</span>
                  </InfoRow>
                </div>
              }
            >
              <span className="text-xs tabular-nums text-muted-foreground">
                {formatRelative(pastOrNow(device.last_fetched_at), i18n.language)}
              </span>
              {canDelete && (
                <IconButton
                  size="8"
                  tone="danger"
                  data-row-button
                  disabled={isBusy}
                  onClick={() => void run(() => deleteDevice(device.hwid), t("subscriptions.devices.deleted"))}
                >
                  <Trash2 className="size-4" />
                </IconButton>
              )}
            </Row>
          ))}
      </DialogCard>
    </div>
  );
}
