// Uygulama kökü hata sınırı (SEC-11, CWE-248): bir bileşen render sırasında istisna fırlatırsa
// beyaz sayfa yerine "Sayfa açılamadı" ve ana sayfa bağlantısı gösterilir. App, sınırı URL
// karmasıyla anahtarlar; başka bir sayfaya geçince hata durumu sıfırlanır.
import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";
import { I18nContext } from "../i18n";
import type { I18nValue } from "../i18n";

interface State {
  failed: boolean;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  static override contextType = I18nContext;
  override state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  override componentDidCatch(error: unknown, info: ErrorInfo): void {
    // Ayrıntı yalnızca geliştirici konsoluna; kullanıcıya iç hata metni gösterilmez.
    console.warn("FinSight AI: sayfa render edilemedi", error, info.componentStack);
  }

  override render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    const { t } = this.context as I18nValue;
    return (
      <div className="page">
        <div className="state-card state-error crash-card" role="alert">
          <div className="state-body">
            <h1 className="state-title crash-title">{t("state.crashTitle")}</h1>
            <p className="state-text">{t("state.crashBody")}</p>
          </div>
          <a className="btn btn-primary" href="#/">
            {t("state.backHome")}
          </a>
        </div>
      </div>
    );
  }
}
