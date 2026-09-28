import { Figure, FigurePct, Ring, Caption } from "./primitives";
import { stationInk } from "../lib/theme";
import type { Summary } from "../lib/stats";

const AMBER = "#ff9a4d";
const SAGE = "#9bbf8a";

function secs(ms: number): string {
  if (!ms) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

// 运行质量仪表组: 成功率 / 并发效率 / 平均延迟。各用不同载体, 不重样。
export function RuntimeGauges({ s }: { s: Summary }) {
  const rt = s.runtime;
  if (!rt.hasRuntime) {
    return (
      <div className="rounded-lg border border-dashed border-line px-5 py-8 text-center font-mono text-[11px] leading-relaxed text-paper-faint">
        运行质量字段(延迟 / 回退 / 并发)尚未接入
        <br />
        待 router 侧 ledger 汇总完善后自动点亮 · 当前数据源仅含产出计数
      </div>
    );
  }
  return (
    <div className="grid grid-cols-2 items-center gap-x-6 gap-y-8 sm:grid-cols-4">
      {/* 成功率环 */}
      <div className="flex flex-col items-center">
        <Ring pct={s.rate} color={SAGE} size={108} stroke={7}>
          <FigurePct value={s.rate} className="font-display text-[24px] font-bold text-paper" />
        </Ring>
        <span className="mt-3 kicker">成功率</span>
      </div>
      {/* 并发效率环 (占位字段, 真实墙钟未接入时显示 —) */}
      <div className="flex flex-col items-center">
        <Ring pct={rt.concEff > 0 ? Math.min(100, (rt.concEff / 16) * 100) : 0} color={AMBER} size={108} stroke={7}>
          {rt.concEff > 0 ? (
            <span className="font-display text-[24px] font-bold tabular-nums text-paper">
              <Figure value={rt.concEff} /><span className="text-[15px] text-paper-dim">×</span>
            </span>
          ) : (
            <span className="font-display text-[24px] font-bold text-paper-faint">—</span>
          )}
        </Ring>
        <span className="mt-3 kicker">并发效率<span className="ml-1 text-paper-faint">*</span></span>
      </div>
      {/* 平均延迟 (大数读数) */}
      <div className="flex flex-col items-center justify-center">
        <div className="font-display text-[40px] font-bold leading-none tracking-tightest text-paper">
          {secs(rt.avgLatencyMs)}
        </div>
        <span className="mt-3 kicker">每张出图延迟</span>
      </div>
      {/* 回退率 */}
      <div className="flex flex-col items-center justify-center">
        <div className="font-display text-[40px] font-bold leading-none tracking-tightest text-paper">
          {rt.fallbackRate}<span className="text-[20px] text-paper-dim">%</span>
        </div>
        <span className="mt-3 kicker">回退率 · {rt.fallbacks} 次</span>
      </div>
      <div className="col-span-2 mt-1 font-mono text-[9.5px] leading-relaxed text-paper-faint sm:col-span-4">
        * 并发效率 = 批次内平均并行起跑数, 当前为占位口径; 待 router 补「批次墙钟 / 理论单图时长」后切换为真实并行加速比。
      </div>
    </div>
  );
}

// 中转站记分牌: 卡片矩阵 (非表格), 首位高亮; 点击下钻聚焦该站。
export function StationBoard({
  s,
  activeStation,
  onSelect,
}: {
  s: Summary;
  activeStation?: string | null;
  onSelect?: (station: string) => void;
}) {
  const stations = s.stations;
  const hasRt = s.runtime.hasRuntime;
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <Caption tag="Relay Scoreboard">中转站记分牌 · 按产出</Caption>
        <span className="font-mono text-[11px] text-paper-faint">{stations.length} 站</span>
      </div>
      {stations.length === 0 ? (
        <div className="mt-5 font-mono text-[11px] text-paper-faint">该范围暂无站点数据</div>
      ) : (
        <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {stations.map((st, i) => {
            const color = stationInk(st.station);
            const lead = i === 0 && st.img > 0;
            const active = activeStation === st.station;
            const dimmed = !!activeStation && !active;
            return (
              <button
                type="button"
                key={st.station}
                onClick={() => onSelect?.(st.station)}
                title={`点击聚焦 ${st.station} 站点`}
                className={`reveal relative overflow-hidden rounded-lg border bg-white/[0.012] px-4 py-3.5 text-left transition-all duration-300 hover:bg-white/[0.03] active:scale-[0.99] ${
                  dimmed ? "opacity-40 saturate-[0.6]" : "opacity-100"
                }`}
                style={{
                  borderColor: active ? `${color}cc` : lead ? `${color}66` : "rgba(236,232,225,0.10)",
                  animationDelay: `${Math.min(i * 70, 420)}ms`,
                }}
              >
                {lead && (
                  <span
                    className="absolute right-0 top-0 px-2 py-0.5 font-mono text-[8.5px] tracking-widest"
                    style={{ background: `${color}22`, color }}
                  >
                    LEAD
                  </span>
                )}
                <div className="flex items-center gap-2">
                  <span
                    className="h-2 w-2 rounded-[2px]"
                    style={{ background: color, boxShadow: lead ? `0 0 7px ${color}` : undefined }}
                  />
                  <code className="font-mono text-[12.5px] text-paper">{st.station}</code>
                  <span className="ml-auto font-mono text-[10px] text-paper-faint">{st.share}%</span>
                </div>
                <div className="mt-3 flex items-end gap-1.5">
                  <Figure
                    value={st.img}
                    className="font-display text-[30px] font-bold leading-none tracking-tightest text-paper"
                  />
                  <span className="mb-1 font-mono text-[10px] text-paper-faint">张</span>
                </div>
                <div className="mt-3 flex items-center gap-x-4 gap-y-1 font-mono text-[10.5px] text-paper-faint">
                  <span>成功率 <span className="text-sage">{st.rate}%</span></span>
                  {hasRt && <span>延迟 <span className="text-paper-dim">{secs(st.avgLatencyMs)}</span></span>}
                  {hasRt && <span>回退 <span className="text-paper-dim">{st.fallbackRate}%</span></span>}
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
