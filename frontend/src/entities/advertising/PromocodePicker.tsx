import { Ticket } from "lucide-react";
import { useTranslation } from "react-i18next";

import { listPromocodes, PICKER_PAGE_SIZE } from "@entities/promocodes/api";
import { useAsyncResource } from "@shared/lib/use-async-resource";
import { SelectDropdown } from "@shared/ui/SelectDropdown";

const NONE = "";

interface PromocodePickerProps {
  id: string;
  value: number | null;
  onChange: (promocodeId: number | null) => void;
}

/** The promocode an ad link applies to whoever opens it; shown only with PROMOCODES_VIEW. */
export function PromocodePicker({ id, value, onChange }: PromocodePickerProps) {
  const { t } = useTranslation();
  const [state] = useAsyncResource((signal) =>
    listPromocodes({ status: "ACTIVE", pageSize: PICKER_PAGE_SIZE }, signal),
  );
  const promocodes = state.kind === "ready" ? state.data.items : [];
  const options = [
    { value: NONE, label: t("advertising.promocode.none") },
    ...promocodes.map((promo) => ({ value: String(promo.id), label: promo.code })),
  ];
  // a linked code that has since expired is still shown as the current choice; while the list loads it's just "#id"
  if (value !== null && !promocodes.some((promo) => promo.id === value)) {
    options.push({
      value: String(value),
      label: state.kind === "ready" ? t("advertising.promocode.unavailable", { id: value }) : `#${value}`,
    });
  }

  return (
    <SelectDropdown
      variant="well"
      size="compact"
      id={id}
      value={value === null ? NONE : String(value)}
      onChange={(selected) => onChange(selected === NONE ? null : Number(selected))}
      icon={Ticket}
      placeholder={t("advertising.promocode.none")}
      options={options}
    />
  );
}
