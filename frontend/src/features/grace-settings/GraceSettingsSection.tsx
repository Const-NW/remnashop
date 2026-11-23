import { Hourglass, Power } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { getGraceSettings, updateGraceSettings, type GraceSettings } from "@entities/settings/api";
import type { TrafficLimitStrategy } from "@entities/subscriptions/api";
import { usePermissions } from "@entities/user/use-current-user";
import { PlanSquadsField } from "@features/plan-edit/PlanSquadsField";
import { getErrorMessage } from "@shared/api/http";
import { blockNonIntegerKeys, parseRequiredInt } from "@shared/lib/number-input";
import { toast } from "@shared/lib/toast";
import { useAsyncResource } from "@shared/lib/use-async-resource";
import { ChoiceChip } from "@shared/ui/ChoiceChip";
import { ErrorState } from "@shared/ui/glass/EmptyState";
import { Field } from "@shared/ui/glass/Field";
import { Input } from "@shared/ui/glass/Input";
import { Row } from "@shared/ui/glass/Row";
import { Section, SectionFields, SectionRows } from "@shared/ui/glass/Section";
import { Switch } from "@shared/ui/glass/Switch";
import { SaveFooter } from "@shared/ui/SaveFooter";
import { Spinner } from "@shared/ui/Spinner";

const STRATEGIES: TrafficLimitStrategy[] = ["NO_RESET", "DAY", "WEEK", "MONTH", "MONTH_ROLLING"];
const MB_PER_GB = 1024;

type TrafficUnit = "MB" | "GB";

function splitTraffic(mb: number): { value: string; unit: TrafficUnit } {
  return mb > 0 && mb % MB_PER_GB === 0
    ? { value: String(mb / MB_PER_GB), unit: "GB" }
    : { value: String(mb), unit: "MB" };
}

export function GraceSettingsSection() {
  const { t } = useTranslation();
  const canManage = usePermissions().includes("GRACE_SETTINGS_MANAGE");
  const [state, , setData] = useAsyncResource(getGraceSettings);
  const [enabled, setEnabled] = useState(false);
  const [squads, setSquads] = useState<Pick<GraceSettings, "internal_squads" | "external_squad">>({
    internal_squads: [],
    external_squad: null,
  });
  const [traffic, setTraffic] = useState("0");
  const [unit, setUnit] = useState<TrafficUnit>("MB");
  const [strategy, setStrategy] = useState<TrafficLimitStrategy>("NO_RESET");
  const [tag, setTag] = useState("");
  const [days, setDays] = useState("0");
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (state.kind !== "ready") return;
    const split = splitTraffic(state.data.traffic_limit_mb);
    setEnabled(state.data.enabled);
    setSquads({ internal_squads: state.data.internal_squads, external_squad: state.data.external_squad });
    setTraffic(split.value);
    setUnit(split.unit);
    setStrategy(state.data.traffic_limit_strategy);
    setTag(state.data.tag ?? "");
    setDays(String(state.data.duration_days));
  }, [state]);

  const parsedTraffic = parseRequiredInt(traffic, 0);
  const parsedDays = parseRequiredInt(days, 0);
  const trafficMb = parsedTraffic === undefined ? undefined : parsedTraffic * (unit === "GB" ? MB_PER_GB : 1);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (trafficMb === undefined || parsedDays === undefined) return;
    setIsSubmitting(true);
    try {
      const settings = await updateGraceSettings({
        enabled,
        internal_squads: squads.internal_squads,
        external_squad: squads.external_squad,
        traffic_limit_mb: trafficMb,
        traffic_limit_strategy: strategy,
        tag: tag.trim() || null,
        duration_days: parsedDays,
      });
      setData(settings);
      toast.success(t("common.saved"));
    } catch (err) {
      toast.error(getErrorMessage(err, t("common.errors.generic")));
    } finally {
      setIsSubmitting(false);
    }
  };

  const saved = state.kind === "ready" ? state.data : null;
  const dirty =
    saved !== null &&
    (enabled !== saved.enabled ||
      squads.internal_squads.join() !== saved.internal_squads.join() ||
      squads.external_squad !== saved.external_squad ||
      trafficMb !== saved.traffic_limit_mb ||
      strategy !== saved.traffic_limit_strategy ||
      (tag.trim() || null) !== saved.tag ||
      parsedDays !== saved.duration_days);

  return (
    <form onSubmit={(event) => void handleSubmit(event)}>
      <Section
        icon={Hourglass}
        title={t("settings.grace.title")}
        description={t("settings.grace.description")}
        footer={state.kind === "ready" && canManage && <SaveFooter dirty={dirty} saving={isSubmitting} />}
      >
        {state.kind === "loading" && <Spinner size="card" />}
        {state.kind === "error" && <ErrorState text={t("common.loadError")} />}
        {state.kind === "ready" && (
          <fieldset disabled={!canManage} className="contents">
            <SectionRows>
              <Row icon={Power} label={t("settings.grace.enabledLabel")} hint={t("settings.grace.enabledHint")}>
                <Switch checked={enabled} onChange={setEnabled} />
              </Row>
            </SectionRows>
            <SectionFields flush>
              <PlanSquadsField
                value={squads}
                onChange={setSquads}
                keepHints={{
                  internal: t("settings.grace.internalSquadsHint"),
                  external: t("settings.grace.externalSquadHint"),
                }}
              />
              <Field
                label={t("settings.grace.trafficLabel")}
                htmlFor="grace-traffic"
                error={trafficMb === undefined ? t("errors.validation.min", { min: 0 }) : undefined}
              >
                <div className="flex items-center gap-2">
                  <Input
                    id="grace-traffic"
                    className="flex-1"
                    type="number"
                    onKeyDown={blockNonIntegerKeys}
                    step="1"
                    min="0"
                    value={traffic}
                    onChange={(event) => setTraffic(event.target.value)}
                  />
                  {(["MB", "GB"] as const).map((value) => (
                    <ChoiceChip className="h-9" key={value} active={unit === value} onClick={() => setUnit(value)}>
                      {t(`settings.grace.unit.${value}`)}
                    </ChoiceChip>
                  ))}
                </div>
              </Field>
              <Field label={t("plans.edit.strategyLabel")}>
                <div className="flex flex-wrap gap-1.5">
                  {STRATEGIES.map((value) => (
                    <ChoiceChip className="h-9" key={value} active={strategy === value} onClick={() => setStrategy(value)}>
                      {t(`plans.edit.strategy.${value}`)}
                    </ChoiceChip>
                  ))}
                </div>
              </Field>
              <Field label={t("plans.edit.tagLabel")} htmlFor="grace-tag" info={t("settings.grace.tagHint")}>
                <Input
                  id="grace-tag"
                  value={tag}
                  maxLength={16}
                  mono
                  onChange={(event) => setTag(event.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, ""))}
                />
              </Field>
              <Field
                label={t("settings.grace.durationLabel")}
                htmlFor="grace-days"
                hint={parsedDays === 0 ? t("settings.grace.durationUnlimited") : undefined}
                error={parsedDays === undefined ? t("errors.validation.min", { min: 0 }) : undefined}
              >
                <Input
                  id="grace-days"
                  type="number"
                  onKeyDown={blockNonIntegerKeys}
                  step="1"
                  min="0"
                  value={days}
                  onChange={(event) => setDays(event.target.value)}
                />
              </Field>
            </SectionFields>
          </fieldset>
        )}
      </Section>
    </form>
  );
}
