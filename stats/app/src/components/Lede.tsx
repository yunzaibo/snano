import { MODEL } from "../lib/theme";
import type { Summary, RangeKey, SizeStat } from "../lib/stats";

const RANGE_WORD: Record<RangeKey, string> = {
  today: "今日",
  "7d": "过去七天",
  all: "全部区间",
};

// 内联微型趋势条 (编辑式 sparkline): 点多时均匀抽样到 ≤30 根, 末根高亮。
function Spark({ values, color }: { values: number[]; color: string }) {
  if (!values.length) return null;
  const cap = 30;
  const vals =
    values.length > cap
      ? Array.from({ length: cap }, (_, i) => values[Math.round((i * (values.length - 1)) / (cap - 1))])
      : values;
  const max = Math.max(1, ...vals);
  return (
    <span className="ml-2 inline-flex h-[15px] items-end gap-[2px] align-[-3px]">
      {vals.map((v, i) => (
        <span
          key={i}
          className="block w-[3px] rounded-[1px]"
          style={{
            height: `${v > 0 ? Math.max(10, (v / max) * 100) : 6}%`,
            background: color,
            opacity: i === vals.length - 1 ? 1 : v > 0 ? 0.5 : 0.18,
          }}
        />
      ))}
    </span>
  );
}

function strong(text: string) {
  return <span className="font-semibold text-paper">{text}</span>;
}

// 编辑导语 (Editor's Note): 把本范围数据写成一句杂志式 standfirst + 内联趋势。
// 数据静止时, 用"编辑视角"制造活力, 而非假动画。
export function Lede({ s }: { s: Summary }) {
  const rw = RANGE_WORD[s.range] || "本范围";

  if (s.empty || s.totalImg === 0) {
    return (
      <div>
        <div className="kicker">Editor&rsquo;s Note · 本期导览</div>
        <p className="mt-2 font-display text-[15px] leading-relaxed text-paper-faint">
          {rw}，台面尚未显影 —— 等待第一张落定。
        </p>
      </div>
    );
  }

  const lead = [...s.models].sort((a, b) => b.img - a.img)[0];
  const leadName = lead ? MODEL[lead.line]?.name ?? lead.line : "";
  const station = s.stations[0];
  const domSize: SizeStat | undefined = [...s.sizes].sort((a, b) => b.img - a.img)[0];
  const sparkColor = lead ? MODEL[lead.line]?.color ?? "#ff9a4d" : "#ff9a4d";

  // 站点子句: 1 站独力 / 多站协同。
  const stationClause =
    s.stations.length <= 1
      ? station
        ? <>中转站 {strong(station.station)} 独力承担</>
        : null
      : <>{strong(`${s.stations.length}`)} 座中转站协同，{strong(station.station)} 出力最多</>;

  return (
    <div>
      <div className="kicker">Editor&rsquo;s Note · 本期导览</div>
      <p className="mt-2 font-display text-[15px] leading-relaxed text-paper-dim md:text-[16px]">
        {rw}，{leadName && <>{strong(leadName)} 领衔（{strong(`${lead.share}%`)}）</>}
        ，共 {strong(`${s.totalImg}`)} 张落台
        {stationClause && <>，{stationClause}</>}
        {domSize && <>，{strong(domSize.size)} 分辨率为主</>}
        ，成功率 {strong(`${s.rate}%`)}。
        <Spark values={s.series.map((p) => p.value)} color={sparkColor} />
      </p>
    </div>
  );
}
