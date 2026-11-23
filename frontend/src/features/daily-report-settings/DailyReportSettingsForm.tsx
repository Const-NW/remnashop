import { ChartColumn, Clock, Globe } from "lucide-react";
import { useEffect, useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { getNotificationSettings, updateNotificationSettings } from "@entities/notifications/api";
import { usePermissions } from "@entities/user/use-current-user";
import { getErrorMessage } from "@shared/api/http";
import { toast } from "@shared/lib/toast";
import { useAsyncResource } from "@shared/lib/use-async-resource";
import { ErrorState } from "@shared/ui/glass/EmptyState";
import { Field } from "@shared/ui/glass/Field";
import { Section, SectionFields } from "@shared/ui/glass/Section";
import { SaveFooter } from "@shared/ui/SaveFooter";
import { SelectDropdown } from "@shared/ui/SelectDropdown";
import { Spinner } from "@shared/ui/Spinner";

const TIME_ZONES = ["UTC", ...Intl.supportedValuesOf("timeZone").filter((zone) => zone !== "UTC")].map((zone) => ({
  value: zone,
  label: zone,
}));
const HOURS = Array.from({ length: 24 }, (_, hour) => ({
  value: String(hour),
  label: `${String(hour).padStart(2, "0")}:00`,
}));

/** When the daily report goes out; who gets it is each admin's "Reports" switch in their profile. */
export function DailyReportSettingsForm() {
  const { t } = useTranslation();
  const canManage = usePermissions().includes("NOTIFICATION_SETTINGS_MANAGE");
  const [state, , setData] = useAsyncResource(getNotificationSettings);
  const [timezone, setTimezone] = useState("UTC");
  const [hour, setHour] = useState("0");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const id = useId();

  useEffect(() => {
    if (state.kind !== "ready") return;
    setTimezone(state.data.report_timezone);
    setHour(String(state.data.daily_report_hour));
  }, [state]);

  const saved = state.kind === "ready" ? state.data : null;
  const dirty =
    saved !== null && (timezone !== saved.report_timezone || hour !== String(saved.daily_report_hour));

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setIsSubmitting(true);
    try {
      setData(await updateNotificationSettings({ report_timezone: timezone, daily_report_hour: Number(hour) }));
      toast.success(t("common.saved"));
    } catch (err) {
      toast.error(getErrorMessage(err, t("common.errors.generic")));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form onSubmit={(event) => void handleSubmit(event)}>
      <Section
        icon={ChartColumn}
        title={t("settings.dailyReport.title")}
        description={t("settings.dailyReport.description")}
        footer={state.kind === "ready" && canManage && <SaveFooter dirty={dirty} saving={isSubmitting} />}
      >
        {state.kind === "loading" && <Spinner size="card" />}
        {state.kind === "error" && <ErrorState text={t("common.loadError")} />}
        {state.kind === "ready" && (
          <SectionFields>
            <Field label={t("settings.dailyReport.timezone")} htmlFor={`${id}-timezone`}>
              <SelectDropdown
                variant="well"
                id={`${id}-timezone`}
                disabled={!canManage}
                value={timezone}
                onChange={setTimezone}
                icon={Globe}
                placeholder={timezone}
                options={TIME_ZONES}
              />
            </Field>
            <Field label={t("settings.dailyReport.hour")} htmlFor={`${id}-hour`}>
              <SelectDropdown
                variant="well"
                id={`${id}-hour`}
                disabled={!canManage}
                value={hour}
                onChange={setHour}
                icon={Clock}
                placeholder={HOURS[Number(hour)].label}
                options={HOURS}
              />
            </Field>
          </SectionFields>
        )}
      </Section>
    </form>
  );
}
