import type { RangeKey } from "../lib/stats";

const RANGE_STAMP: Record<RangeKey, string> = { today: "24H", "7d": "7D", all: "ALL" };
const MARK = "rgba(236,232,225,0.16)";

// 印相纸边 / 暗房套准: 四角裁切角标 + 顶部套准十字 + 侧边帧号 stamp。
// 纯装饰、pointer-events-none, 极低成本; md 以下隐藏避免拥挤。
export function Margins({ range }: { range: RangeKey }) {
  return (
    <div className="pointer-events-none absolute inset-2 z-20 hidden md:block" aria-hidden>
      {/* 四角裁切角标 */}
      <span className="absolute left-0 top-0 h-3.5 w-3.5 border-l border-t" style={{ borderColor: MARK }} />
      <span className="absolute right-0 top-0 h-3.5 w-3.5 border-r border-t" style={{ borderColor: MARK }} />
      <span className="absolute bottom-0 left-0 h-3.5 w-3.5 border-b border-l" style={{ borderColor: MARK }} />
      <span className="absolute bottom-0 right-0 h-3.5 w-3.5 border-b border-r" style={{ borderColor: MARK }} />

      {/* 顶部中线套准十字 */}
      <span
        className="absolute left-1/2 top-[-7px] -translate-x-1/2 font-mono text-[11px] leading-none"
        style={{ color: MARK }}
      >
        ✛
      </span>

      {/* 右侧竖排帧号 stamp (胶片边缘字样) */}
      <span
        className="absolute right-[-2px] top-1/2 origin-right -translate-y-1/2 rotate-90 font-mono text-[8.5px] tracking-[0.34em] text-paper-faint"
      >
        DEVELOP · № {RANGE_STAMP[range] || ""}
      </span>
    </div>
  );
}
