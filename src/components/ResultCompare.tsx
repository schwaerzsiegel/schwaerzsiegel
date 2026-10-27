import { useRef, useState } from "react";

interface Props {
  beforeUrl: string;
  afterUrl: string;
  afterLabel: string;
  // Anzeigebreite in Pixeln (Phase 1: passend zum Zoom der Seitenansicht,
  // unabhängig von der Render-Auflösung der Bilder)
  width?: number;
}

export function ResultCompare({ beforeUrl, afterUrl, afterLabel, width }: Props) {
  const imageStyle = width ? { width } : undefined;
  const containerRef = useRef<HTMLDivElement>(null);
  const [percent, setPercent] = useState(50);
  const dragging = useRef(false);

  function updateFromClientX(clientX: number) {
    const el = containerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const pct = ((clientX - rect.left) / rect.width) * 100;
    setPercent(Math.min(100, Math.max(0, pct)));
  }

  return (
    <div className="compare-frame">
      <div className="compare-caption-bar">
        <div className="compare-caption-half compare-caption-original" style={{ flexBasis: `${percent}%` }}>
          Original
        </div>
        <div className="compare-caption-half compare-caption-after" style={{ flexBasis: `${100 - percent}%` }}>
          {afterLabel}
        </div>
      </div>

      <div
        ref={containerRef}
        className="compare-image-area"
        onMouseDown={(e) => {
          dragging.current = true;
          updateFromClientX(e.clientX);
        }}
        onMouseMove={(e) => dragging.current && updateFromClientX(e.clientX)}
        onMouseUp={() => (dragging.current = false)}
        onMouseLeave={() => (dragging.current = false)}
      >
        <img src={beforeUrl} alt="Original" className="compare-image" style={imageStyle} draggable={false} />
        <div className="compare-after" style={{ clipPath: `inset(0 0 0 ${percent}%)` }}>
          <img src={afterUrl} alt={afterLabel} className="compare-image" style={imageStyle} draggable={false} />
        </div>

        <div className="compare-handle" style={{ left: `${percent}%` }}>
          <span className="compare-handle-grip">⇔</span>
        </div>
      </div>
    </div>
  );
}
