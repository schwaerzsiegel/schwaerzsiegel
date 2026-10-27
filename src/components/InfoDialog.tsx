import thirdParty from "../assets/drittanbieter.json";
import { APP_VERSION, COPYRIGHT, SOURCE_CONTACT, SOURCE_FILE, SOURCE_URL, type BrandAssets } from "./BrandBar";
import { Modal } from "./Modal";

interface ThirdPartyItem {
  name: string;
  version: string;
  license: string;
  note?: string;
}

// Phase 4: Info-Dialog mit Version, Arbeitsweise und – per Branding –
// Support-Kontakt und Impressum des Anbieters.
// Phase 6: Lizenzen verwendeter Software (erzeugt mit tools/lizenzhinweise.py).
// Phase 11: Hinweis auf die AGPL, Gewährleistungsausschluss und Quellcode
// (AGPL Abschnitt 0 „Appropriate Legal Notices“).
export function InfoDialog({ assets, onClose }: { assets: BrandAssets; onClose: () => void }) {
  const { branding } = assets;
  const support = branding.support;
  return (
    <Modal
      title={branding.productName}
      onClose={onClose}
      footer={
        <button className="btn" onClick={onClose}>
          Schließen
        </button>
      }
    >
      <dl className="info-grid">
        {branding.customerName && (
          <>
            <dt>Anbieter</dt>
            <dd>{branding.customerName}</dd>
          </>
        )}
        <dt>Version</dt>
        <dd className="mono">Schwärzsiegel {APP_VERSION}</dd>
        <dt>Verarbeitung</dt>
        <dd>ausschließlich lokal auf diesem Rechner; Netzwerkzugriffe sind gesperrt</dd>
      </dl>
      {support && (
        <section style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <h3 className="side-title">Support</h3>
          <dl className="info-grid">
            {support.name && (
              <>
                <dt>Kontakt</dt>
                <dd>{support.name}</dd>
              </>
            )}
            {support.email && (
              <>
                <dt>E-Mail</dt>
                {/* bewusst kein Link: die App öffnet keine externen Programme oder Seiten */}
                <dd>{support.email}</dd>
              </>
            )}
            {support.phone && (
              <>
                <dt>Telefon</dt>
                <dd>{support.phone}</dd>
              </>
            )}
            {support.url && (
              <>
                <dt>Web</dt>
                <dd>{support.url}</dd>
              </>
            )}
          </dl>
          {support.text && <p style={{ whiteSpace: "pre-line", fontSize: 13, color: "var(--ink-2)" }}>{support.text}</p>}
        </section>
      )}
      <section style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <h3 className="side-title">Lizenz</h3>
        <p style={{ fontSize: 13, color: "var(--ink-2)", margin: 0 }}>
          {COPYRIGHT}. Schwärzsiegel ist freie Software: Sie dürfen es unter den Bedingungen der GNU Affero
          General Public License, Version 3 oder (nach Ihrer Wahl) jeder späteren Version, weitergeben und
          verändern. Die Lizenz selbst enthält keine Gewährleistung. Haben Sie Schwärzsiegel im Rahmen eines
          Pilots oder mit dem Schwärzsiegel-Paket erhalten, gelten zusätzlich die Bedingungen Ihrer
          Vereinbarung. Den Lizenztext finden Sie in der Datei „LICENSE.txt“ im Installationsordner.
        </p>
        <p style={{ fontSize: 13, color: "var(--ink-2)", margin: 0 }}>
          Quellcode: Der vollständige Quellcode liegt dem Installationspaket als Datei{" "}
          <span className="mono">{SOURCE_FILE}</span> bei und ist öffentlich unter{" "}
          <span className="mono">{SOURCE_URL}</span> verfügbar. Auf Anfrage erhalten Sie ihn auch per E-Mail an{" "}
          {SOURCE_CONTACT}.
        </p>
      </section>
      <section style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <h3 className="side-title">Verwendete Software und Modelle</h3>
        <p style={{ fontSize: 13, color: "var(--ink-2)", margin: 0 }}>
          Die vollständigen Lizenztexte stehen in der Datei „DRITTANBIETER-LIZENZEN.txt“ im Installationsordner.
        </p>
        {thirdParty.groups.map((group) => (
          <details key={group.title} className="third-party">
            <summary>
              {group.title} ({group.items.length})
            </summary>
            <ul>
              {(group.items as ThirdPartyItem[]).map((item) => (
                <li key={`${item.name}@${item.version}`}>
                  <span>
                    {item.name}
                    {item.version && <span className="mono"> {item.version}</span>}
                    {item.note && <span className="muted"> – {item.note}</span>}
                  </span>
                  <span className="muted">{item.license}</span>
                </li>
              ))}
            </ul>
          </details>
        ))}
      </section>
      {branding.warnings.length > 0 && (
        <div className="hint-box">
          <strong>Hinweise zur Anpassung</strong>
          <ul>
            {branding.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
    </Modal>
  );
}
