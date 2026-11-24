import type { ParseKeys } from "i18next";
import { BookOpen } from "lucide-react";
import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import {
  createTestPayment,
  getTestPaymentStatus,
  setGatewayActive,
  updateGatewayChannels,
  updateGatewaySettings,
  type Gateway,
  type GatewayChannels,
  type GatewayFieldSpec,
  type TestPaymentChannel,
  type TestPaymentStatus,
} from "@entities/gateways/api";
import { usePermissions } from "@entities/user/use-current-user";
import { getErrorMessage } from "@shared/api/http";
import { isHttpsUrl } from "@shared/lib/external-links";
import { toast } from "@shared/lib/toast";
import { Button } from "@shared/ui/Button";
import { Collapse } from "@shared/ui/Collapse";
import { Field } from "@shared/ui/glass/Field";
import { CopyInput, Input, PasswordInput } from "@shared/ui/glass/Input";
import { Row } from "@shared/ui/glass/Row";
import { Switch } from "@shared/ui/glass/Switch";
import { Pressable } from "@shared/ui/Pressable";
import { SaveFooter } from "@shared/ui/SaveFooter";

import { ChannelTiles } from "./ChannelTiles";
import { GATEWAY_DOCS_URLS } from "./docs-links";
import { GATEWAY_LOGOS } from "./logos";
import { inputModeFor, sanitizeFieldValue } from "./field-input";
import { PaygateGuideDialog } from "./PaygateGuideDialog";

const CURRENCY_HINT_GATEWAYS = new Set([
  "CRYPTOMUS",
  "HELEKET",
  "CRYPTOPAY",
  "WATA",
  "VALUTIX",
  "RIOPAY",
  "SEVERPAY",
  "CLOUDPAYMENTS",
  "PAYPALYCH",
  "CISPAY",
  "PAY_2328",
]);

const TEST_PAYMENT_POLL_MS = 3000;

interface GatewaySettingsCardProps {
  gateway: Gateway;
  fields: GatewayFieldSpec[];
  webhookUrl: string | null;
  onChanged: (gateway: Gateway) => void;
}

export function GatewaySettingsCard({
  gateway,
  fields,
  webhookUrl,
  onChanged,
}: GatewaySettingsCardProps) {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isToggling, setIsToggling] = useState(false);
  const [savingFlag, setSavingFlag] = useState<string | null>(null);
  const [isTesting, setIsTesting] = useState(false);
  const [testOrderId, setTestOrderId] = useState<number | null>(null);
  const [testStatus, setTestStatus] = useState<TestPaymentStatus | null>(null);

  const label = t(`settings.gateways.types.${gateway.type}` as ParseKeys);
  const flagFields = fields.filter((field) => field.kind === "boolean" && field.group === null);
  const valueFields = fields.filter((field) => field.kind !== "boolean");
  const ungroupedValueFields = valueFields.filter((field) => field.group === null);
  const isGroupOn = (key: string) => values[key] === "true" || Boolean(gateway.flags[key]);
  // a group: its switch, then the fields it shows while on
  const groups = fields
    .filter((field) => field.kind === "boolean" && field.group === field.key)
    // kept while on, so a channel switched off doesn't hide a switch still in use
    .filter(
      (toggle) =>
        !toggle.requires_all_channels ||
        (gateway.telegram_enabled && gateway.cabinet_enabled) ||
        isGroupOn(toggle.key),
    )
    .map((toggle) => ({
      toggle,
      fields: valueFields.filter((field) => field.group === toggle.key),
    }));
  const docsUrl = GATEWAY_DOCS_URLS[gateway.type];
  const logoUrl = GATEWAY_LOGOS[gateway.type];
  const dirty = Object.values(values).some((value) => value !== "");
  const permissions = usePermissions();
  const canUpdate = permissions.includes("GATEWAYS_UPDATE");
  const canSetChannels = permissions.includes("GATEWAYS_CHANNELS_UPDATE");
  const canTest = gateway.is_configured && permissions.includes("GATEWAYS_TEST");
  const bothChannels = gateway.telegram_enabled && gateway.cabinet_enabled;
  // both channels in one shop although the gateway could split them (a saved group would)
  const sharedShop =
    bothChannels &&
    gateway.is_configured &&
    groups.some((group) => group.toggle.requires_all_channels) &&
    !groups.some((group) => gateway.flags[group.toggle.key]);
  const testsPerChannel = bothChannels && gateway.splits_channels;
  // with one channel on, its shop
  const testChannel: TestPaymentChannel = gateway.telegram_enabled ? "BOT" : "CABINET";

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const changed = Object.fromEntries(Object.entries(values).filter(([, value]) => value !== ""));
    if (Object.keys(changed).length === 0) return;

    setIsSubmitting(true);
    try {
      onChanged(await updateGatewaySettings(gateway.type, changed));
      setValues({});
      toast.success(t("common.saved"));
    } catch (err) {
      toast.error(getErrorMessage(err, t("common.errors.generic")));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleFlag = async (key: string, enabled: boolean) => {
    setSavingFlag(key);
    try {
      onChanged(await updateGatewaySettings(gateway.type, { [key]: String(enabled) }));
    } catch (err) {
      toast.error(getErrorMessage(err, t("common.errors.generic")));
    } finally {
      setSavingFlag(null);
    }
  };

  const handleChannels = async (channels: GatewayChannels) => {
    setSavingFlag("channels");
    try {
      onChanged(await updateGatewayChannels(gateway.type, channels));
    } catch (err) {
      toast.error(getErrorMessage(err, t("common.errors.generic")));
    } finally {
      setSavingFlag(null);
    }
  };

  const handleToggle = async () => {
    setIsToggling(true);
    try {
      onChanged(await setGatewayActive(gateway.type, !gateway.is_active));
    } catch (err) {
      toast.error(getErrorMessage(err, t("settings.gateways.toggleError")));
    } finally {
      setIsToggling(false);
    }
  };

  useEffect(() => {
    if (testOrderId === null || testStatus !== "PENDING") return;
    const timer = setInterval(() => {
      if (document.hidden) return;
      getTestPaymentStatus(testOrderId)
        .then(({ status }) => {
          if (status === "PENDING") return;
          setTestStatus(status);
          if (status === "PAID") toast.success(t("settings.gateways.testPaymentPaid"));
        })
        .catch(() => {});
    }, TEST_PAYMENT_POLL_MS);
    return () => clearInterval(timer);
  }, [testOrderId, testStatus, t]);

  const handleTestPayment = async (channel: TestPaymentChannel) => {
    // opened synchronously so the popup blocker treats it as user-initiated
    const tab = window.open("", "_blank");
    setIsTesting(true);
    try {
      const { order_id, confirmation_url } = await createTestPayment(gateway.type, channel);
      setTestOrderId(order_id);
      setTestStatus("PENDING");
      if (tab && confirmation_url && isHttpsUrl(confirmation_url)) {
        tab.opener = null;
        tab.location.href = confirmation_url;
      } else {
        tab?.close();
        if (confirmation_url) toast.error(t("settings.gateways.testPaymentBadUrl"));
      }
    } catch (err) {
      tab?.close();
      toast.error(getErrorMessage(err, t("settings.gateways.testPaymentError")));
    } finally {
      setIsTesting(false);
    }
  };

  const testButton = canTest && (
    <>
      {testsPerChannel ? (
        <>
          <Button variant="glass" disabled={isTesting} onClick={() => void handleTestPayment("BOT")}>
            {t("settings.gateways.testPaymentTelegram")}
          </Button>
          <Button
            variant="glass"
            disabled={isTesting}
            onClick={() => void handleTestPayment("CABINET")}
          >
            {t("settings.gateways.testPaymentCabinet")}
          </Button>
        </>
      ) : (
        <Button variant="glass" isLoading={isTesting} onClick={() => void handleTestPayment(testChannel)}>
          {isTesting ? t("settings.gateways.testPaymentCreating") : t("settings.gateways.testPayment")}
        </Button>
      )}
      {testStatus !== null && (
        <span
          className={`text-xs ${
            testStatus === "PAID"
              ? "text-success"
              : testStatus === "PENDING"
                ? "text-muted-foreground"
                : "text-warning"
          }`}
        >
          {t(`settings.gateways.testPaymentStatus.${testStatus}`)}
        </span>
      )}
    </>
  );

  // switching a group on only opens its fields: the switch is saved with them,
  // since the server refuses it alone on a working gateway
  const handleGroupToggle = (key: string, enabled: boolean) => {
    if (gateway.flags[key]) {
      if (!enabled) void handleFlag(key, false);
      return;
    }
    setValues((prev) => {
      const { [key]: _, ...rest } = prev;
      return enabled ? { ...prev, [key]: "true" } : rest;
    });
  };

  const renderFlag = (
    field: GatewayFieldSpec,
    checked = gateway.flags[field.key] ?? false,
    onChange: (enabled: boolean) => void = (enabled) => void handleFlag(field.key, enabled),
  ) => (
    // 2px past the panel's 14px: the switch under the row's own (16px in)
    <div key={field.key} className="flex items-center gap-3 pr-0.5">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground">
          {t(`settings.gateways.fields.${field.key}` as ParseKeys)}
        </p>
        <p className="text-xs text-muted-foreground">
          {t(`settings.gateways.flagHints.${field.key}` as ParseKeys)}
        </p>
      </div>
      {field.key === "relay_enabled" && <PaygateGuideDialog webhookUrl={webhookUrl} />}
      <Switch
        checked={checked}
        onChange={onChange}
        disabled={!canUpdate || savingFlag === field.key}
      />
    </div>
  );

  const renderValueField = (field: GatewayFieldSpec) => {
    // once configured, a blank field keeps the stored value
    const waived = field.required_unless !== null && isGroupOn(field.required_unless);
    // a group being switched on is saved with its fields, even on a working gateway
    const mustFill =
      !waived &&
      (field.group !== null
        ? isGroupOn(field.group) && (!gateway.flags[field.group] || !gateway.is_configured)
        : (field.required || field.required_unless !== null) && !gateway.is_configured);
    const inputProps = {
      id: `gateway-${gateway.type}-${field.key}`,
      inputMode: inputModeFor(field),
      value: values[field.key] ?? "",
      onChange: (event: ChangeEvent<HTMLInputElement>) =>
        setValues((prev) => ({
          ...prev,
          [field.key]: sanitizeFieldValue(field, event.target.value),
        })),
      placeholder: t("settings.gateways.placeholder"),
      disabled: !canUpdate,
    };
    return (
      <Field
        key={field.key}
        label={t(`settings.gateways.fields.${field.key}` as ParseKeys)}
        htmlFor={inputProps.id}
        required={mustFill}
        info={
          field.key === "currency" && CURRENCY_HINT_GATEWAYS.has(gateway.type)
            ? t(`settings.gateways.currencyHints.${gateway.type}` as ParseKeys)
            : undefined
        }
      >
        {field.kind === "secret" ? (
          // "off" is ignored on password fields: the browser would offer the admin's saved password
          <PasswordInput {...inputProps} autoComplete="new-password" />
        ) : (
          <Input {...inputProps} autoComplete="off" />
        )}
      </Field>
    );
  };

  return (
    <Row
      glyph={
        logoUrl ? (
          <img src={logoUrl} alt="" className="size-[22px] object-contain" />
        ) : (
          <span className="text-sm font-semibold">{label.charAt(0)}</span>
        )
      }
      label={label}
      description={
        <span className={`flex items-center gap-1.5 ${gateway.is_configured ? "text-success" : "text-warning"}`}>
          <span className="size-1.5 rounded-full bg-current" />
          {t(gateway.is_configured ? "settings.gateways.configured" : "settings.gateways.notConfigured")}
        </span>
      }
      expanded={isOpen}
      onClick={() => setIsOpen((open) => !open)}
      onSubmit={(event) => void handleSubmit(event)}
      panel={
        <>
          <ChannelTiles
            gateway={gateway}
            // ignored mid-save rather than disabled: dimming both tiles reads as a flicker
            onChange={(channels) => savingFlag !== "channels" && void handleChannels(channels)}
            disabled={!canSetChannels}
            sharedShop={sharedShop}
          />
          {flagFields.map((field) => renderFlag(field))}
          {webhookUrl !== null && (
            <Field label={t("settings.gateways.webhookLabel")} htmlFor={`gateway-${gateway.type}-webhook`}>
              <CopyInput id={`gateway-${gateway.type}-webhook`} value={webhookUrl} />
            </Field>
          )}
          {ungroupedValueFields.map(renderValueField)}
          {groups.map(({ toggle, fields: groupFields }) => (
            <div key={toggle.key} className="flex flex-col gap-3 border-t border-white/[0.08] pt-3">
              {renderFlag(toggle, isGroupOn(toggle.key), (enabled) => handleGroupToggle(toggle.key, enabled))}
              <Collapse open={isGroupOn(toggle.key)} className="flex flex-col gap-3">
                {groupFields.map(renderValueField)}
              </Collapse>
            </div>
          ))}
          {valueFields.length > 0 && canUpdate ? (
            <SaveFooter flush dirty={dirty} saving={isSubmitting} beside={testButton} />
          ) : (
            testButton && <div className="flex flex-wrap items-center gap-3">{testButton}</div>
          )}
        </>
      }
    >
      {docsUrl && (
        <Pressable
          as="a"
          href={docsUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex size-8 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-white/[0.06] hover:text-foreground"
        >
          <BookOpen className="size-3.5" />
        </Pressable>
      )}
      <Switch
        checked={gateway.is_active}
        onChange={() => void handleToggle()}
        disabled={!permissions.includes("GATEWAYS_ACTIVE_SET") || isToggling || !gateway.is_configured}
      />
    </Row>
  );
}
