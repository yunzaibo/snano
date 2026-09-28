import { Figure, SegMeter, Caption } from "./primitives";
import { lineInk, MODEL } from "../lib/theme";
import type { Summary } from "../lib/stats";

function stationOf(provider: string): string {
  return provider.split("-", 1)[0] || "—";
}

export function Lanes({ s }: { s: Summary }) {
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <Caption tag="Provider Lanes">单通道明细 · 站点 × 模型</Caption>
        <span className="font-mono text-[11px] text-paper-faint">{s.lanes.length} lanes</span>
      </div>
      <div className="mt-5">
        {s.empty ? (
          <div className="py-10 text-center font-mono text-[12px] text-paper-faint">
            该时间范围暂无产出
          </div>
        ) : (
          s.lanes.map((l, i) => {
            const color = lineInk(l.line);
            const lead = i === 0 && l.imageCount > 0;
            const station = stationOf(l.provider);
            const model = MODEL[l.line]?.name || l.line;
            return (
              <div
                key={l.provider}
                className="reveal grid grid-cols-[22px_1fr_auto] items-center gap-x-4 gap-y-2 border-b border-line-2 py-3.5 md:grid-cols-[22px_minmax(190px,1fr)_1.1fr_auto]"
                style={{ animationDelay: `${Math.min(i * 45, 360)}ms` }}
              >
                <span
                  className="font-mono text-[12px] tabular-nums"
                  style={{ color: lead ? color : "rgba(107,101,92,1)" }}
                >
                  {String(l.index).padStart(2, "0")}
                </span>

                {/* 站点 · 模型 (可读, 不截断); 原始 lane id 作弱化副标题 */}
                <div className="flex min-w-0 flex-col gap-0.5">
                  <div className="flex items-center gap-2">
                    <span
                      className="h-2 w-2 shrink-0 rounded-[2px]"
                      style={{ background: color, boxShadow: lead ? `0 0 7px ${color}` : undefined }}
                    />
                    <span className="font-mono text-[12.5px] text-paper-dim">{station}</span>
                    <span className="text-line">·</span>
                    <span className={`font-display font-bold leading-none ${lead ? "text-[14px] text-paper" : "text-[13px] text-paper"}`}>
                      {model}
                    </span>
                    {lead && <span className="kicker shrink-0" style={{ color }}>LEAD</span>}
                  </div>
                  <code className="truncate font-mono text-[10px] text-paper-faint">{l.provider}</code>
                </div>

                <div className="hidden md:block">
                  <SegMeter pct={s.maxLaneImg ? (l.imageCount / s.maxLaneImg) * 100 : 0} color={color} />
                </div>

                <div className="flex items-center gap-4 justify-self-end font-mono text-[12.5px] tabular-nums">
                  <span className={lead ? "font-display text-[17px] font-bold text-paper" : "text-paper"}>
                    <Figure value={l.imageCount} /> 张
                  </span>
                  <span className="text-sage" title="成功请求">{l.successCount}</span>
                  <span className="text-brick" title="失败请求">{l.failedCount}</span>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
