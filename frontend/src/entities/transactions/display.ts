import type { ParseKeys, TFunction } from "i18next";

/** A free order went through no gateway: null, or the one an old-version row names. */
export function transactionGatewayLabel(gatewayType: string | null, t: TFunction): string {
  return gatewayType ? t(`settings.gateways.types.${gatewayType}` as ParseKeys) : t("transactions.free");
}
