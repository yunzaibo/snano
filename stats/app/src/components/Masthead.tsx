import { RangeTabs } from "./RangeTabs";
import type { RangeKey } from "../lib/stats";

function RegMark() {
  return (
    <svg width="30" height="30" viewBox="0 0 30 30" fill="none" className="shrink-0">
      <circle cx="15" cy="15" r="9.5" stroke="#ff9a4d" strokeWidth="1.2" />
      <path d="M15 1.5v9M15 19.5v9M1.5 15h9M19.5 15h9" stroke="#ff9a4d" strokeWidth="1.2" />
      <circle cx="15" cy="15" r="2" fill="#6fe0bf" />
    </svg>
  );
}

export function Masthead({
  source,
  live,
  range,
  onRange,
}: {
  source: string;
  live: boolean;
  range: RangeKey;
  onRange: (v: RangeKey) => void;
}) {
  return (
    <div className="flex items-end justify-between gap-6">
      <div className="flex items-center gap-3.5">
        <RegMark />
        <div>
          <div className="flex items-baseline gap-2.5 font-display text-[17px] font-semibold tracking-tight text-paper">
            显影台
            <span className="kicker">Light Table</span>
          </div>
          <div className="mt-1 font-mono text-[11px] text-paper-faint">
            生图产出图鉴 · snano / simage ·{" "}
            <span className="text-paper-dim">{source}</span>
            {live && <span className="ml-1.5 text-mint">· 实时</span>}
          </div>
        </div>
      </div>
      <RangeTabs value={range} onChange={onRange} />
    </div>
  );
}
