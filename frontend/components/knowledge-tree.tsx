"use client";

import type { KnowledgeTreeNode } from "../lib/types";

export function KnowledgeTree(props: {
  node: KnowledgeTreeNode;
  selectedId: string;
  onSelect: (nodeId: string) => void;
  depth?: number;
}) {
  const depth = props.depth ?? 0;
  const isSelected = props.selectedId === props.node.id;

  return (
    <div>
      <button
        className={
          "flex w-full items-center justify-between rounded-xl border px-3 py-2 text-left text-sm transition " +
          (isSelected
            ? "border-amber-300/60 bg-amber-300/10"
            : "border-transparent hover:border-slate-700 hover:bg-slate-900")
        }
        style={{ paddingLeft: 12 + depth * 14 }}
        onClick={() => props.onSelect(props.node.id)}
      >
        <span>{props.node.name}</span>
        <span className="text-xs text-slate-500">{props.node.document_count}</span>
      </button>
      {props.node.children.length > 0 && (
        <div className="mt-1 space-y-1">
          {props.node.children.map((child) => (
            <KnowledgeTree
              key={child.id}
              node={child}
              selectedId={props.selectedId}
              onSelect={props.onSelect}
              depth={depth + 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}
