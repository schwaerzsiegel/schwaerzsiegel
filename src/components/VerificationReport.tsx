import type { Verification } from "../api/client";
import { pageOfProblem } from "../lib/workflow";
import { Icon } from "./Icon";

// Prüfbericht nach dem Schwärzen (Phase 1, Gestaltung Phase 4). Bewusst ohne
// "100 % sicher"-Aussage: die Prüfungen sind automatisch und haben Grenzen.
export function VerificationReport({
  verification,
  actions,
  onJumpToPage,
}: {
  verification: Verification;
  actions?: string[];
  onJumpToPage?: (page: number) => void;
}) {
  const passed = verification.passed;
  return (
    <>
      <div className={`report-banner ${passed ? "is-passed" : "is-failed"}`} role={passed ? "status" : "alert"}>
        <Icon name={passed ? "okCircle" : "failCircle"} size={28} strokeWidth={2} />
        <div className="report-banner-text">
          <strong>{passed ? "Automatische Prüfung ohne Befund" : "Prüfung nicht bestanden – Export gesperrt"}</strong>
          {passed ? (
            <span>
              Die gespeicherte Datei wurde neu geöffnet und unabhängig geprüft. Bitte das Ergebnis trotzdem selbst ansehen – die
              Prüfung erkennt nicht jede denkbare Informationsquelle.
            </span>
          ) : (
            <>
              <span>Das Ergebnis wurde verworfen und kann nicht gespeichert werden. Gründe:</span>
              <ul className="report-problems">
                {verification.problems.map((p, i) => {
                  const page = pageOfProblem(p);
                  return (
                    <li key={i}>
                      {p}
                      {page !== null && onJumpToPage && (
                        <>
                          {" "}
                          <button className="text-link" onClick={() => onJumpToPage(page)}>
                            Zur Stelle springen
                          </button>
                        </>
                      )}
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>
      </div>

      <section className="report-section">
        <h3 className="side-title">Einzelprüfungen</h3>
        <ul className="check-list">
          {verification.checks.map((c) => (
            <li key={c.key}>
              <span className={`check-icon ${c.ok ? "ok" : "fail"}`}>
                <Icon name={c.ok ? "check" : "close"} size={13} strokeWidth={2.4} />
              </span>
              <span className="check-label">{c.label}</span>
              <span className="check-detail">{c.detail}</span>
            </li>
          ))}
        </ul>
      </section>

      {verification.warnings.length > 0 && (
        <section className="hint-box">
          <strong>Hinweis{verification.warnings.length > 1 ? "e" : ""}</strong>
          <ul>
            {verification.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </section>
      )}

      {actions && actions.length > 0 && (
        <section className="report-section">
          <h3 className="side-title">Bereinigt</h3>
          <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{actions.join(" · ")}</span>
        </section>
      )}
    </>
  );
}
