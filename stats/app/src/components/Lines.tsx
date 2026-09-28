import { Figure, Ring } from "./primitives";
import { MODEL } from "../lib/theme";
import { pct } from "../lib/utils";
import type { Summary, ModelStat, Line } from "../lib/stats";

function ModelRing({
  m,
  total,
  className,
  active,
  dimmed,
  onSelect,
}: {
  m: ModelStat;
  total: number;
  className?: string;
  active?: boolean;
  dimmed?: boolean;
  onSelect?: () => void;
}) {
  const meta = MODEL[m.line];
  const color = meta.color;
  return (
    <button
      type="button"
      onClick={onSelect}
      title={`点击聚焦 ${meta.name} 产线`}
      className={`group flex items-center gap-6 rounded-xl border px-4 py-3 text-left transition-all duration-300 active:scale-[0.99] ${
        active ? "border-current" : "border-transparent hover:border-line"
      } ${dimmed ? "opacity-40 saturate-[0.6]" : "opacity-100"} ${className || ""}`}
      style={{ color: active ? color : undefined }}
    >
      <Ring pct={m.share} color={color}>
        <Figure value={m.img} className="font-display text-[30px] font-bold leading-none text-paper" />
        <span className="mt-1 font-mono text-[10px] text-paper-faint">张</span>
      </Ring>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-[2px]" style={{ background: color }} />
          <span className="font-display text-[15px] font-bold leading-none text-paper">{meta.name}</span>
          {active && (
            <span className="ml-1 font-mono text-[8.5px] tracking-widest" style={{ color }}>● 聚焦中</span>
          )}
        </div>
        <div className="mt-1.5 font-mono text-[10px] text-paper-faint">{meta.vendor} · {m.line}</div>
        <div className="mt-3 font-display text-[26px] font-bold leading-none text-paper">{pct(m.img, total)}</div>
        <div className="mt-1 font-mono text-[10px] text-paper-faint">占总产出</div>
        <div className="mt-4 font-mono text-[11px] text-paper-faint">
          成功 <span className="text-sage">{m.ok}</span>
          <span className="mx-1.5 text-line">/</span>
          失败 <span className="text-brick">{m.fail}</span>
          <span className="mx-1.5 text-line">·</span>
          {m.rate}%
        </div>
      </div>
    </button>
  );
}

export function Lines({
  s,
  focus,
  onSelect,
}: {
  s: Summary;
  focus?: Line | null;
  onSelect?: (line: Line) => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      {[s.models[0], s.models[1]].map((m) => (
        <ModelRing
          key={m.line}
          m={m}
          total={s.totalImg}
          active={focus === m.line}
          dimmed={!!focus && focus !== m.line}
          onSelect={() => onSelect?.(m.line)}
        />
      ))}
    </div>
  );
}
