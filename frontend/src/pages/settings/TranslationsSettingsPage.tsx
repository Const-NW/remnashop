import { usePermissions } from "@entities/user/use-current-user";
import { TranslationsEditorForm } from "@features/translations-editor/TranslationsEditorForm";

export function TranslationsSettingsPage() {
  const permissions = usePermissions();
  return (
    <div className="flex flex-col gap-5">
      {permissions.includes("TRANSLATIONS_VIEW") && <TranslationsEditorForm />}
    </div>
  );
}
