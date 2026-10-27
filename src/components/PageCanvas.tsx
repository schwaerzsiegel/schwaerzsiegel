import { useEffect, useMemo, useRef, useState } from "react";
import type { Mark, PageInfo } from "../api/client";
import {
  clientToPage,
  containsPoint,
  handleAt,
  handlePoint,
  HANDLES,
  isUsable,
  moveRect,
  normalizeRect,
  resizeRect,
  sameRects,
  wordIndexAt,
  wordsBetween,
  type Handle,
  type Point,
  type Rect,
} from "../lib/geometry";
import { CATEGORIES, markTitle } from "../lib/markLabels";
import { Icon } from "./Icon";

export type Tool = "select" | "rect" | "text";

interface Props {
  imageUrl: string | null;
  page: PageInfo;
  marks: Mark[];
  scale: number;
  tool: Tool;
  selectedId: string | null;
  hoveredId: string | null;
  readOnly?: boolean;
  onSelect: (id: string | null) => void;
  onHover: (id: string | null) => void;
  onCreateArea: (rect: Rect) => void;
  onCreateText: (wordIds: string[]) => void;
  onChangeRects: (markId: string, rects: Rect[], previous: Rect[]) => void;
  // Phase 4: Aktionsleiste an der ausgewählten Markierung
  onStatus?: (mark: Mark, status: "confirmed" | "rejected") => void;
  onLabel?: (mark: Mark, label: string) => void;
  onDelete?: (mark: Mark) => void;
}

type Drag =
  | { type: "draw"; start: Point; current: Point }
  | { type: "move" | "resize"; markId: string; index: number; handle: Handle | null; start: Point; original: Rect[]; rects: Rect[] }
  | { type: "text"; anchor: number; focus: number };

/** Über der Markierung, bei zu wenig Platz darunter. */
function selectionBarPosition(rects: Rect[], pageHeight: number, px: (v: number) => number): React.CSSProperties {
  const top = Math.min(...rects.map((r) => r[1]));
  const bottom = Math.max(...rects.map((r) => r[3]));
  const left = Math.min(...rects.map((r) => r[0]));
  const above = px(top) - 52;
  return { left: Math.max(0, px(left)), top: above >= 0 ? above : Math.min(px(bottom) + 10, px(pageHeight) - 48) };
}

function SelectionBar({
  mark,
  style,
  onStatus,
  onLabel,
  onDelete,
}: {
  mark: Mark;
  style: React.CSSProperties;
  onStatus: (mark: Mark, status: "confirmed" | "rejected") => void;
  onLabel?: (mark: Mark, label: string) => void;
  onDelete?: (mark: Mark) => void;
}) {
  const known = CATEGORIES.includes(mark.label);
  const confirmed = mark.status === "confirmed" || mark.status === "redacted";
  return (
    <div
      className="floating-bar"
      role="toolbar"
      aria-label="Aktionen für die ausgewählte Markierung"
      style={style}
      onPointerDown={(e) => e.stopPropagation()}
    >
      {onLabel ? (
        <select aria-label="Kategorie" value={known ? mark.label : "__current"} onChange={(e) => onLabel(mark, e.target.value)}>
          {!known && <option value="__current">{mark.label}</option>}
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      ) : (
        <span className="floating-bar-label">{mark.label}</span>
      )}
      {confirmed ? (
        <button onClick={() => onStatus(mark, "rejected")}>Verwerfen</button>
      ) : (
        <button className="is-primary" onClick={() => onStatus(mark, "confirmed")}>
          <Icon name="check" size={16} />
          Bestätigen
        </button>
      )}
      {onDelete && (
        <button onClick={() => onDelete(mark)} aria-label="Markierung löschen (Entf)" title="Löschen (Entf)">
          <Icon name="trash" size={16} />
        </button>
      )}
    </div>
  );
}

// Phase 1: Seitenansicht mit Werkzeugen. Alle Koordinaten sind Seiten-
// koordinaten in Punkten (Anzeige, Rotation schon eingerechnet); auf den
// Bildschirm kommen sie nur über `scale`. Eine Markierung wird hier nur
// vorgemerkt - geschwärzt wird erst nach Freigabe und "Schwärzen".
export function PageCanvas(props: Props) {
  const { page, marks, scale, tool, selectedId, hoveredId, readOnly } = props;
  const surfaceRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  // Textauswahl bleibt nach dem Loslassen stehen, bis sie übernommen oder
  // verworfen wird.
  const [textSelection, setTextSelection] = useState<{ anchor: number; focus: number } | null>(null);

  useEffect(() => {
    setTextSelection(null);
    setDrag(null);
  }, [page.index, tool]);

  const selectedWords = useMemo(() => {
    const sel = drag?.type === "text" ? drag : textSelection;
    return sel ? wordsBetween(page.words, sel.anchor, sel.focus) : [];
  }, [drag, textSelection, page.words]);

  function confirmText() {
    if (selectedWords.length) props.onCreateText(selectedWords.map((w) => w.id));
    setTextSelection(null);
  }

  useEffect(() => {
    if (!textSelection) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Enter") {
        e.preventDefault();
        confirmText();
      } else if (e.key === "Escape") {
        setTextSelection(null);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  function pointFrom(e: React.PointerEvent): Point {
    const box = surfaceRef.current!.getBoundingClientRect();
    const [x, y] = clientToPage(e.clientX, e.clientY, box, scale);
    return [Math.max(0, Math.min(x, page.width)), Math.max(0, Math.min(y, page.height))];
  }

  // Oberste Markierung unter dem Zeiger; die ausgewählte hat Vorrang.
  function markAt(p: Point): { mark: Mark; index: number } | null {
    const ordered = [...marks].sort((a, b) => (a.id === selectedId ? 1 : 0) - (b.id === selectedId ? 1 : 0));
    for (let i = ordered.length - 1; i >= 0; i--) {
      const m = ordered[i];
      const index = m.rects.findIndex((r) => containsPoint(r, p, 1 / scale));
      if (index >= 0) return { mark: m, index };
    }
    return null;
  }

  function onPointerDown(e: React.PointerEvent) {
    if (readOnly || e.button !== 0) return;
    const p = pointFrom(e);
    e.currentTarget.setPointerCapture(e.pointerId);

    if (tool === "rect") {
      setDrag({ type: "draw", start: p, current: p });
      return;
    }
    if (tool === "text") {
      const index = wordIndexAt(page.words, p);
      setTextSelection(null);
      if (index >= 0) setDrag({ type: "text", anchor: index, focus: index });
      return;
    }
    // Auswahlwerkzeug: zuerst Anfasser der ausgewählten Markierung
    const selected = marks.find((m) => m.id === selectedId);
    if (selected) {
      for (let i = 0; i < selected.rects.length; i++) {
        const handle = handleAt(selected.rects[i], p, scale);
        if (handle) {
          setDrag({ type: "resize", markId: selected.id, index: i, handle, start: p,
                    original: selected.rects, rects: selected.rects });
          return;
        }
      }
    }
    const hit = markAt(p);
    if (!hit) {
      props.onSelect(null);
      return;
    }
    props.onSelect(hit.mark.id);
    setDrag({ type: "move", markId: hit.mark.id, index: hit.index, handle: null, start: p,
              original: hit.mark.rects, rects: hit.mark.rects });
  }

  function onPointerMove(e: React.PointerEvent) {
    if (!drag) {
      if (tool === "select") props.onHover(markAt(pointFrom(e))?.mark.id ?? null);
      return;
    }
    const p = pointFrom(e);
    if (drag.type === "draw") {
      setDrag({ ...drag, current: p });
    } else if (drag.type === "text") {
      const index = wordIndexAt(page.words, p, 6);
      if (index >= 0 && index !== drag.focus) setDrag({ ...drag, focus: index });
    } else {
      const dx = p[0] - drag.start[0];
      const dy = p[1] - drag.start[1];
      const base = drag.original[drag.index];
      const changed = drag.type === "move"
        ? moveRect(base, dx, dy, page.width, page.height)
        : resizeRect(base, drag.handle!, dx, dy, page.width, page.height);
      setDrag({ ...drag, rects: drag.original.map((r, i) => (i === drag.index ? changed : r)) });
    }
  }

  function onPointerUp() {
    if (!drag) return;
    if (drag.type === "draw") {
      const rect = normalizeRect(drag.start, drag.current);
      if (isUsable(rect)) props.onCreateArea(rect);
    } else if (drag.type === "text") {
      setTextSelection({ anchor: drag.anchor, focus: drag.focus });
    } else if (!sameRects(drag.rects, drag.original)) {
      props.onChangeRects(drag.markId, drag.rects, drag.original);
    }
    setDrag(null);
  }

  const px = (v: number) => v * scale;
  const boxStyle = (r: Rect): React.CSSProperties => ({
    left: px(r[0]),
    top: px(r[1]),
    width: px(r[2] - r[0]),
    height: px(r[3] - r[1]),
  });

  const draftRects = (m: Mark): Rect[] =>
    drag && (drag.type === "move" || drag.type === "resize") && drag.markId === m.id ? drag.rects : m.rects;

  const lastWord = selectedWords[selectedWords.length - 1];
  const selected = marks.find((m) => m.id === selectedId);

  return (
    <div
      ref={surfaceRef}
      className={`page-surface tool-${tool} ${readOnly ? "is-readonly" : ""}`}
      style={{ width: px(page.width), height: px(page.height) }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={() => setDrag(null)}
      onPointerLeave={() => !drag && props.onHover(null)}
    >
      {props.imageUrl && <img className="page-image" src={props.imageUrl} alt={`Seite ${page.index + 1}`} draggable={false} />}

      {marks.map((m) =>
        draftRects(m).map((r, i) => (
          <div
            key={`${m.id}-${i}`}
            className={`mark-box status-${m.status} ${m.id === selectedId ? "is-selected" : ""} ${m.id === hoveredId ? "is-hovered" : ""}`}
            style={boxStyle(r)}
            title={markTitle(m)}
          />
        )),
      )}

      {selected && !readOnly && tool === "select" &&
        draftRects(selected).map((r, i) =>
          HANDLES.map((h) => {
            const [hx, hy] = handlePoint(r, h);
            return <div key={`${i}-${h}`} className={`mark-handle handle-${h}`} style={{ left: px(hx), top: px(hy) }} />;
          }),
        )}

      {selectedWords.map((w) => (
        <div key={w.id} className="text-selection" style={boxStyle(w.bbox)} />
      ))}

      {drag?.type === "draw" && <div className="draft-rect" style={boxStyle(normalizeRect(drag.start, drag.current))} />}

      {textSelection && lastWord && (
        <div
          className="floating-bar"
          role="toolbar"
          aria-label="Textauswahl"
          style={{ left: px(lastWord.bbox[0]), top: px(lastWord.bbox[3]) + 6 }}
          onPointerDown={(e) => e.stopPropagation()}
        >
          <button className="is-primary" onClick={confirmText}>
            Zur Schwärzung hinzufügen
          </button>
          <button onClick={() => setTextSelection(null)}>Abbrechen</button>
        </div>
      )}

      {selected && !readOnly && !drag && !textSelection && props.onStatus && (
        <SelectionBar
          mark={selected}
          style={selectionBarPosition(draftRects(selected), page.height, px)}
          onStatus={props.onStatus}
          onLabel={props.onLabel}
          onDelete={props.onDelete}
        />
      )}
    </div>
  );
}
