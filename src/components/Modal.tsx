import { useEffect, useRef } from "react";
import { Icon } from "./Icon";

// Phase 4: schlichter Dialog. Escape schließt, der Fokus springt in den
// Dialog und beim Schließen zurück.
export function Modal({
  title,
  onClose,
  children,
  footer,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    // Fokus auf das erste Eingabefeld, sonst auf die Hauptaktion - nicht auf "Schließen"
    const root = ref.current;
    const first =
      root?.querySelector<HTMLElement>(".modal-body input:checked, .modal-body input, .modal-body select") ??
      root?.querySelector<HTMLElement>(".modal-foot .btn-primary") ??
      root?.querySelector<HTMLElement>(".modal-foot button");
    first?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    }
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      previous?.focus?.();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
        <div className="modal-head">
          <h2 id="modal-title">{title}</h2>
          <button className="icon-btn is-plain" onClick={onClose} aria-label="Schließen">
            <Icon name="close" />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}
