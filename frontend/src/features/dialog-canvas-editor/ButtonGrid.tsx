import {
  DndContext,
  DragOverlay,
  MeasuringStrategy,
  PointerSensor,
  closestCenter,
  defaultDropAnimationSideEffects,
  getFirstCollision,
  pointerWithin,
  useDndContext,
  useDroppable,
  useSensor,
  useSensors,
  type CollisionDetection,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
  type DropAnimation,
  type Modifier,
  type UniqueIdentifier,
} from "@dnd-kit/core";
import { SortableContext, arrayMove, useSortable, horizontalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS, getEventCoordinates } from "@dnd-kit/utilities";
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

import { buttonKey, iconIdTextKey, iconTextKey, splitButtonIcon } from "@entities/dialogs/api";
import { EmojiThumbnail } from "@features/dialog-canvas-editor/EmojiThumbnail";
import type { DialogButtonItem } from "@entities/dialogs/api";
import type { Selection } from "@features/dialog-canvas-editor/DialogCanvasEditor";

interface ButtonGridProps {
  buttons: DialogButtonItem[];
  textByKey: Record<string, string>;
  selection: Selection | null;
  onSelect: (selection: Selection) => void;
  emptyLabel: string;
  canEdit: boolean;
  onReorder: (next: DialogButtonItem[]) => void;
}

/** row id → button ids in column order; `row-new` is the row a drop can open below the rest */
type Rows = Record<string, string[]>;

const NEW_ROW = "row-new";

const DROP_ANIMATION: DropAnimation = {
  sideEffects: defaultDropAnimationSideEffects({ styles: { active: { opacity: "0.3" } } }),
};

// Telegram's keyboard buttons: a translucent fill over the chat, tinted by style
const STYLE_CLASSNAME: Record<string, string> = {
  primary: "bg-primary/25",
  success: "bg-success/25",
  danger: "bg-error/25",
};

const buttonId = buttonKey;

// The lifted button shrinks to its text, so it's held by its middle, not where it was grabbed.
const centerOnCursor: Modifier = ({ activatorEvent, draggingNodeRect, transform }) => {
  const pointer = activatorEvent ? getEventCoordinates(activatorEvent) : null;
  if (!pointer || !draggingNodeRect) return transform;
  return {
    ...transform,
    x: transform.x + pointer.x - draggingNodeRect.left - draggingNodeRect.width / 2,
    y: transform.y + pointer.y - draggingNodeRect.top - draggingNodeRect.height / 2,
  };
};

function toRows(buttons: DialogButtonItem[]): Rows {
  const byRow = new Map<number, DialogButtonItem[]>();
  for (const button of buttons) {
    const row = byRow.get(button.row) ?? [];
    row.push(button);
    byRow.set(button.row, row);
  }
  const rows: Rows = {};
  [...byRow.entries()]
    .sort(([a], [b]) => a - b)
    .forEach(([, row], index) => {
      rows[`row-${index}`] = row.sort((a, b) => a.column - b.column).map(buttonId);
    });
  rows[NEW_ROW] = [];
  return rows;
}

function findRow(rows: Rows, id: UniqueIdentifier): string | undefined {
  const key = String(id);
  if (key in rows) return key;
  return Object.keys(rows).find((row) => rows[row].includes(key));
}

/** keys keep insertion order, so the new row stays last; emptied rows drop out */
function renumber(rows: Rows, byId: Map<string, DialogButtonItem>): DialogButtonItem[] {
  const flat: DialogButtonItem[] = [];
  Object.values(rows)
    .filter((ids) => ids.length > 0)
    .forEach((ids, row) => {
      ids.forEach((id, column) => {
        const button = byId.get(id);
        if (button) flat.push({ ...button, row, column });
      });
    });
  return flat;
}

function useButtonLabel(button: DialogButtonItem, textByKey: Record<string, string>) {
  const { t } = useTranslation();
  // An unsaved button has no translation row yet; a saved one can still have an
  // empty label, which the bot refuses to render at all.
  const text = textByKey[button.text_key] ?? "";
  const isUnlabelled = text.trim() === "";
  return {
    isUnlabelled,
    label: isUnlabelled
      ? t(button.id === null ? "dialogs.canvas.newButton" : "dialogs.canvas.unlabelledButton")
      : text,
  };
}

function ButtonFace({
  button,
  textByKey,
  className,
}: {
  button: DialogButtonItem;
  textByKey: Record<string, string>;
  className: string;
}) {
  const { label: rawLabel, isUnlabelled } = useButtonLabel(button, textByKey);
  const styleClassName = button.style ? STYLE_CLASSNAME[button.style] : "bg-white/[0.08]";
  const { text: label, icon: leadingIcon } = splitButtonIcon(rawLabel);
  const icon = leadingIcon?.glyph ?? textByKey[iconTextKey(button.text_key)];
  // a premium icon keeps its plain glyph, drawn if the thumbnail fails
  const iconId = leadingIcon?.customEmojiId ?? textByKey[iconIdTextKey(button.text_key)];

  return (
    <span
      className={`flex h-10 min-w-0 items-center justify-center gap-1.5 rounded-xl px-3 text-center text-sm font-medium text-foreground backdrop-blur-md ${styleClassName} ${className}`}
    >
      {iconId ? (
        <span className="text-sm">
          <EmojiThumbnail glyph={icon ?? ""} customEmojiId={iconId} />
        </span>
      ) : icon ? (
        <span className="text-sm">{icon}</span>
      ) : null}
      <span className={`truncate ${isUnlabelled ? "italic text-muted-foreground" : ""}`}>{label}</span>
    </span>
  );
}

function CanvasButton({
  button,
  textByKey,
  isSelected,
  isEffectivelyHidden,
  onClick,
  draggable,
}: {
  button: DialogButtonItem;
  textByKey: Record<string, string>;
  isSelected: boolean;
  isEffectivelyHidden: boolean;
  onClick: () => void;
  draggable: boolean;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: buttonId(button),
    disabled: !draggable,
  });

  return (
    <button
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        // Without this a touch drag scrolls the page instead of moving the button.
        touchAction: draggable ? "none" : undefined,
      }}
      type="button"
      onClick={() => onClick()}
      {...attributes}
      {...listeners}
      className={`flex min-w-0 flex-1 rounded-xl ${draggable ? "cursor-grab active:cursor-grabbing" : "cursor-pointer"}`}
    >
      <ButtonFace
        button={button}
        textByKey={textByKey}
        className={`w-full transition ${isEffectivelyHidden ? "opacity-45 outline-1 -outline-offset-1 outline-dashed outline-white/25 hover:opacity-70" : ""} ${
          isDragging
            ? // The real button stays as the gap the overlay will drop back into.
              "opacity-25"
            : isSelected
              ? "ring-2 ring-primary"
              : "hover:bg-white/[0.12]"
        }`}
      />
    </button>
  );
}

function Row({
  id,
  buttons,
  textByKey,
  selection,
  onSelect,
  canEdit,
}: {
  id: string;
  buttons: DialogButtonItem[];
  textByKey: Record<string, string>;
  selection: Selection | null;
  onSelect: (selection: Selection) => void;
  canEdit: boolean;
}) {
  // the row itself is a target too, so a button can enter one emptied mid-drag
  const { setNodeRef } = useDroppable({ id });

  return (
    <div ref={setNodeRef} className="flex min-h-10 gap-1.5 rounded-xl">
      <SortableContext items={buttons.map(buttonId)} strategy={horizontalListSortingStrategy}>
        {buttons.map((button) => (
          <CanvasButton
            key={buttonId(button)}
            button={button}
            textByKey={textByKey}
            draggable={canEdit}
            isSelected={selection?.kind === "button" && selection.buttonKey === buttonId(button)}
            isEffectivelyHidden={button.is_hidden}
            onClick={() => onSelect({ kind: "button", buttonKey: buttonId(button) })}
          />
        ))}
      </SortableContext>
    </div>
  );
}

const isInside = (rect: DOMRect, x: number, y: number) =>
  x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;

/** Fits its text in hand; over the rows, once off its own spot, takes the width of the slot it would land in. */
function LiftedButton({
  button,
  textByKey,
  grid,
}: {
  button: DialogButtonItem;
  textByKey: Record<string, string>;
  grid: RefObject<HTMLDivElement | null>;
}) {
  // activeNode is the faded placeholder, already sized by the row it would land in
  const { activeNode, activatorEvent } = useDndContext();
  const [slotWidth, setSlotWidth] = useState<number | null>(null);
  const [isOverRows, setIsOverRows] = useState(false);

  useEffect(() => {
    if (!activeNode) return;
    const observer = new ResizeObserver(() => setSlotWidth(activeNode.getBoundingClientRect().width));
    observer.observe(activeNode);
    return () => observer.disconnect();
  }, [activeNode]);

  useEffect(() => {
    const home = activeNode?.getBoundingClientRect();
    let hasLeftHome = false;
    const onMove = (event: PointerEvent) => {
      if (home && !isInside(home, event.clientX, event.clientY)) hasLeftHome = true;
      const rows = grid.current?.getBoundingClientRect();
      setIsOverRows(hasLeftHome && rows !== undefined && isInside(rows, event.clientX, event.clientY));
    };
    window.addEventListener("pointermove", onMove);
    return () => window.removeEventListener("pointermove", onMove);
    // home is where the drag began: measured once per activation
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activatorEvent]);

  return (
    <div className="flex size-full cursor-grabbing items-center justify-center">
      <span
        style={{ width: isOverRows && slotWidth !== null ? slotWidth : "max-content" }}
        className="flex scale-[1.03] transition-[width] duration-150 ease-out [interpolate-size:allow-keywords]"
      >
        <ButtonFace button={button} textByKey={textByKey} className="w-full shadow-lg shadow-black/40 ring-2 ring-primary/60" />
      </span>
    </div>
  );
}

/** Where a dropped button opens a new row; mounted only during a drag, while that row is empty. */
function NewRowIndicator() {
  const { setNodeRef, isOver } = useDroppable({ id: NEW_ROW });

  return (
    <div ref={setNodeRef} className="flex h-5 items-center">
      <span
        className={`relative h-[3px] w-full rounded-full transition-colors ${
          isOver ? "bg-primary shadow-[0_0_14px_color-mix(in_srgb,var(--color-primary)_55%,transparent)]" : "bg-primary/35"
        }`}
      >
        <span className="absolute -left-0.5 -top-[2.5px] size-2 rounded-full bg-inherit" />
        <span className="absolute -right-0.5 -top-[2.5px] size-2 rounded-full bg-inherit" />
      </span>
    </div>
  );
}

export function ButtonGrid({
  buttons,
  textByKey,
  selection,
  onSelect,
  emptyLabel,
  canEdit,
  onReorder,
}: ButtonGridProps) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }));
  const [draggedId, setDraggedId] = useState<string | null>(null);
  // the layout while a drag moves buttons between rows; null at rest, so props rule
  const [dragRows, setDragRows] = useState<Rows | null>(null);
  const rows = dragRows ?? toRows(buttons);
  const byId = new Map(buttons.map((b) => [buttonId(b), b]));
  const lastOverId = useRef<UniqueIdentifier | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const recentlyMovedToNewRow = useRef(false);

  useEffect(() => {
    requestAnimationFrame(() => {
      recentlyMovedToNewRow.current = false;
    });
  }, [dragRows]);

  // The pointer picks the row, the nearest button in it the slot; between rows the last target
  // is held: a move resizes the buttons, and judging by overlap would flip it back and forth.
  const collisionDetection: CollisionDetection = useCallback(
    (args) => {
      let overId = getFirstCollision(pointerWithin(args), "id");
      if (overId != null) {
        const rowIds = rows[String(overId)];
        if (rowIds && rowIds.length > 0) {
          overId =
            closestCenter({
              ...args,
              droppableContainers: args.droppableContainers.filter((c) => rowIds.includes(String(c.id))),
            })[0]?.id ?? overId;
        }
        lastOverId.current = overId;
        return [{ id: overId }];
      }
      if (recentlyMovedToNewRow.current && draggedId) lastOverId.current = draggedId;
      return lastOverId.current != null ? [{ id: lastOverId.current }] : [];
    },
    [rows, draggedId],
  );

  const handleDragStart = (event: DragStartEvent) => {
    setDraggedId(String(event.active.id));
    setDragRows(toRows(buttons));
  };

  const handleDragOver = ({ active, over }: DragOverEvent) => {
    if (!over) return;
    const activeId = String(active.id);
    const dropped = active.rect.current.translated;
    const isAfter = dropped !== null && dropped.left + dropped.width / 2 > over.rect.left + over.rect.width / 2;
    setDragRows((prev) => {
      const current = prev ?? rows;
      const fromRow = findRow(current, active.id);
      const toRow = findRow(current, over.id);
      if (!fromRow || !toRow || fromRow === toRow) return current;
      const overIds = current[toRow];
      const overIndex = overIds.indexOf(String(over.id));
      const index = overIndex === -1 ? overIds.length : overIndex + (isAfter ? 1 : 0);
      recentlyMovedToNewRow.current = true;
      return {
        ...current,
        [fromRow]: current[fromRow].filter((id) => id !== activeId),
        [toRow]: [...overIds.slice(0, index), activeId, ...overIds.slice(index)],
      };
    });
  };

  const handleDragEnd = ({ active, over }: DragEndEvent) => {
    const row = findRow(rows, active.id);
    const finalRows = rows;
    setDraggedId(null);
    setDragRows(null);
    if (!over || !row) return;
    const ids = finalRows[row];
    const activeIndex = ids.indexOf(String(active.id));
    const overIndex = ids.indexOf(String(over.id));
    const next = overIndex !== -1 && overIndex !== activeIndex ? { ...finalRows, [row]: arrayMove(ids, activeIndex, overIndex) } : finalRows;
    const reordered = renumber(next, byId);
    const changed = reordered.some((b) => {
      const before = byId.get(buttonId(b));
      return before?.row !== b.row || before.column !== b.column;
    });
    if (changed) onReorder(reordered);
  };

  const handleDragCancel = () => {
    setDraggedId(null);
    setDragRows(null);
  };

  if (buttons.length === 0 && !canEdit) {
    return (
      <div className="flex flex-col gap-1.5">
        <p className="text-xs text-muted-foreground">{emptyLabel}</p>
      </div>
    );
  }

  const rowButtons = (key: string) => rows[key].flatMap((id) => byId.get(id) ?? []);
  const draggedButton = draggedId ? byId.get(draggedId) : undefined;

  return (
    <div ref={gridRef} className="flex flex-col gap-1.5">
      <DndContext
        sensors={sensors}
        collisionDetection={collisionDetection}
        measuring={{ droppable: { strategy: MeasuringStrategy.Always } }}
        onDragStart={handleDragStart}
        onDragOver={handleDragOver}
        onDragEnd={handleDragEnd}
        onDragCancel={handleDragCancel}
      >
        {Object.keys(rows)
          .filter((key) => key !== NEW_ROW || rows[key].length > 0)
          .map((key) => (
            <Row
              key={key}
              id={key}
              buttons={rowButtons(key)}
              textByKey={textByKey}
              selection={selection}
              onSelect={onSelect}
              canEdit={canEdit}
            />
          ))}
        {canEdit && draggedId !== null && rows[NEW_ROW].length === 0 && <NewRowIndicator />}
        {/* in body: the panel's backdrop-filter would make it the fixed overlay's containing block, offsetting it by the sidebar */}
        {createPortal(
          <DragOverlay dropAnimation={DROP_ANIMATION} modifiers={[centerOnCursor]}>
            {draggedButton && <LiftedButton button={draggedButton} textByKey={textByKey} grid={gridRef} />}
          </DragOverlay>,
          document.body,
        )}
      </DndContext>
    </div>
  );
}
