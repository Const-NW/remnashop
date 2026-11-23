import { usePermissions } from "@entities/user/use-current-user";
import { AccessSettingsForm } from "@features/access-settings/AccessSettingsForm";
import { DailyReportSettingsForm } from "@features/daily-report-settings/DailyReportSettingsForm";
import { GraceSettingsSection } from "@features/grace-settings/GraceSettingsSection";
import { LegalDocumentsEditor } from "@features/legal-documents-editor/LegalDocumentsEditor";
import { OAuthSettingsForm } from "@features/oauth-settings/OAuthSettingsForm";
import { ShopSettingsForm } from "@features/shop-settings/ShopSettingsForm";
import { SupportSettingsForm } from "@features/support-settings/SupportSettingsForm";
import { WebPushSettingsForm } from "@features/web-push-settings/WebPushSettingsForm";

export function GeneralSettingsPage() {
  const permissions = usePermissions();
  return (
    <div className="flex flex-col gap-5">
      {permissions.includes("OAUTH_SETTINGS_VIEW") && <OAuthSettingsForm />}
      {permissions.includes("ACCESS_SETTINGS_VIEW") && <AccessSettingsForm />}
      {permissions.includes("SHOP_SETTINGS_VIEW") && <ShopSettingsForm />}
      {permissions.includes("GRACE_SETTINGS_VIEW") && <GraceSettingsSection />}
      {permissions.includes("SHOP_SETTINGS_VIEW") && <SupportSettingsForm />}
      {permissions.includes("NOTIFICATION_SETTINGS_VIEW") && <WebPushSettingsForm />}
      {permissions.includes("NOTIFICATION_SETTINGS_VIEW") && <DailyReportSettingsForm />}
      {permissions.includes("LEGAL_DOCUMENTS_VIEW") && <LegalDocumentsEditor />}
    </div>
  );
}
