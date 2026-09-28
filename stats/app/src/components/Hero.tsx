import { Figure, FigurePct } from "./primitives";
import { ProductionChart } from "./ProductionChart";
import type { Summary } from "../lib/stats";

function MetaItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="kicker">{label}</span>
      <span className="font-mono text-[15px] tabular-nums text-paper">{children}</span>
    </div>
  );
}

export function Hero({ s }: { s: Summary }) {
  return (
    <div className="grid grid-cols-12 items-end gap-x-8 gap-y-10">
      <div className="col-span-12 lg:col-span-7">
        <div className="kicker">Images Developed · 本范围生成图片</div>
        <div className="mt-3 flex items-end gap-4">
          <span className="relative inline-flex overflow-hidden">
            <Figure
              value={s.totalImg}
              className="font-display text-[clamp(64px,11vw,140px)] font-bold leading-[0.8] tracking-tightest text-paper"
            />
            <span className="sheen-sweep" aria-hidden />
          </span>
          <span className="mb-3 font-mono text-sm text-paper-faint">张</span>
        </div>
        <div className="mt-8 flex flex-wrap items-end gap-x-10 gap-y-5">
          <MetaItem label="成功">
            <span className="text-sage">
              <Figure value={s.ok} />
            </span>
          </MetaItem>
          <MetaItem label="失败">
            <span className="text-brick">
              <Figure value={s.fail} />
            </span>
          </MetaItem>
          <MetaItem label="成功率">
            <FigurePct value={s.rate} />
          </MetaItem>
          <MetaItem label="批次桶">
            <Figure value={s.buckets} />
          </MetaItem>
        </div>
      </div>
      <div className="col-span-12 lg:col-span-5">
        <ProductionChart
          mode={s.seriesMode}
          series={s.series}
          peak={s.peak}
          peakLabel={s.peakLabel}
        />
      </div>
    </div>
  );
}
