// Bilinmeyen adres.
import { LuSearchX } from "react-icons/lu";
import { EmptyState } from "../components/ui";
import { useI18n } from "../i18n";
import { hrefFor } from "../router";

export function NotFoundPage() {
  const { t } = useI18n();
  return (
    <div className="page">
      <EmptyState
        title={t("state.notFoundTitle")}
        icon={<LuSearchX aria-hidden="true" />}
        action={
          <a className="btn btn-primary" href={hrefFor({ name: "home" })}>
            {t("state.backHome")}
          </a>
        }
      >
        {t("state.notFoundBody")}
      </EmptyState>
    </div>
  );
}
