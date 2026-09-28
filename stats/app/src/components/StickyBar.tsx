import { Figure } from "./primitives";
import { RangeTabs } from "./RangeTabs";
import { MODEL, stationInk } from "../lib/theme";
import type { Summary, RangeKey, Line } from "../lib/stats";

// 粘性概要条: 滚过 Hero 大数字后淡入, 把关键数字 + 成功率 + 聚焦 + range 切换常驻顶部。
export function StickyBar({
  show,
  s,
  range,
  onRange,
  focusLine,
  focusStation,
}: {
  show: boolean;
  s: Summary;
  range: RangeKey;
  onRange: (v: RangeKey) => void;
  focusLine: Line | null;
  focusStation: string | null;
}) {
  const focusLabel = focusLine ? MODEL[focusLine].name : focusStation || null;
  const focusColor = focusLine ? MODEL[focusLine].color : focusStation ? stationInk(focusStation) : "#ff9a4d";
  return (
    <div
      className={`fixed inset-x-0 top-0 z-40 border-b border-line bg-[#100e0b]/80 backdrop-blur-md transition-all duration-300 ${
        show ? "translate-y-0 opacity-100" : "pointer-events-none -translate-y-full opacity-0"
      }`}
    >
      <div className="mx-auto flex max-w-[1080px] items-center gap-x-4 gap-y-1 px-7 py-2.5">
        <span className="font-display text-[13px] font-semibold tracking-tight text-paper">显影台</span>
        <span className="h-3.5 w-px bg-line" />
        <span className="flex items-baseline gap-1 font-display text-[18px] font-bold leading-none text-paper">
          <Figure value={s.totalImg} />
          <span className="text-[11px] font-normal text-paper-faint">张</span>
        </span>
        <span className="font-mono text-[11px] text-paper-faint">
          成功率 <span className="text-sage">{s.rate}%</span>
        </span>
        {focusLabel && (
          <span className="hidden items-center gap-1.5 font-mono text-[11px] text-paper-dim sm:flex">
            <span className="h-1.5 w-1.5 rounded-[2px]" style={{ background: focusColor }} />
            聚焦 {focusLabel}
          </span>
        )}
        <div className="ml-auto">
          <RangeTabs value={range} onChange={onRange} layoutId="range-pill-sticky" />
        </div>
      </div>
    </div>
  );
}
