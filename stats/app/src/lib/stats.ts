// 数据层 —— 与 stats/aggregate.py 的 image-stats/v1 契约一致。
// 前端只消费 records, 与数据是 mock 还是真实 ledger 无关。

export type Line = "snano" | "simage";
export type RequestType = "text_to_image" | "image_to_image";

export interface StatRecord {
  line: Line;
  requestType: RequestType;
  provider: string;
  imageCount: number;
  successCount: number;
  failedCount: number;
  createdAt: string;
  // v2 运行质量维度 (mock 已填; 真实 ledger 接入由 Codex 侧完善, 缺省时面板降级显示)。
  station?: string;
  size?: string;
  latencyMs?: number;
  fallbackCount?: number;
  concFactor?: number;
  // 脱敏错误类别 (来自路由 ProviderErrorCategory: auth/rate_limit/timeout/... )。缺省时日志降级为笼统措辞。
  errorKind?: string;
}

export interface StatsData {
  schemaVersion: string;
  generatedAt: string;
  source: string;
  lineLanes: Record<string, string[]>;
  records: StatRecord[];
}

export type RangeKey = "today" | "7d" | "all";

export const RANGES: { key: RangeKey; label: string; tag: string }[] = [
  { key: "today", label: "今日", tag: "TODAY" },
  { key: "7d", label: "最近 7 天", tag: "7 DAYS" },
  { key: "all", label: "全部", tag: "ALL" },
];

export const FALLBACK_LANES: Record<string, string[]> = {
  snano: ["apiyi-nano-banana-pro-4k", "laozhang-nano-banana-pro-4k-i2i", "apimart-gemini-i2i"],
  simage: ["apiyi-simage-gpt-image-2", "laozhang-simage-gpt-image-2", "apimart-gpt-image-2-i2i"],
};

interface Bucket {
  imageCount: number;
  successCount: number;
  failedCount: number;
}
const emptyBucket = (): Bucket => ({ imageCount: 0, successCount: 0, failedCount: 0 });

function rangeStart(now: Date, range: RangeKey): Date | null {
  if (range === "all") return null;
  const d = new Date(now);
  if (range === "today") {
    d.setHours(0, 0, 0, 0);
    return d;
  }
  // 7d
  d.setDate(d.getDate() - 7);
  return d;
}

export function filterByRange(records: StatRecord[], now: Date, range: RangeKey): StatRecord[] {
  const start = rangeStart(now, range);
  if (!start) return records.slice();
  const s = start.getTime();
  return records.filter((r) => {
    const t = new Date(r.createdAt).getTime();
    return !isNaN(t) && t >= s;
  });
}

function groupSum<K extends string>(rows: StatRecord[], keyFn: (r: StatRecord) => K): Record<K, Bucket> {
  const m = {} as Record<K, Bucket>;
  for (const r of rows) {
    const k = keyFn(r);
    if (!m[k]) m[k] = emptyBucket();
    m[k].imageCount += r.imageCount || 0;
    m[k].successCount += r.successCount || 0;
    m[k].failedCount += r.failedCount || 0;
  }
  return m;
}

const sumField = (rows: StatRecord[], f: keyof Bucket | "imageCount") =>
  rows.reduce((a, r) => a + ((r as any)[f] || 0), 0);

export interface LaneRow extends Bucket {
  line: string;
  provider: string;
  index: number;
}

export type SeriesMode = "hour" | "day";
export interface SeriesPoint {
  label: string;
  value: number;
  current?: boolean; // 当前小时 (hour 模式)
  future?: boolean; // 尚未到达的小时 (hour 模式)
}

export interface ModelStat {
  line: Line;
  img: number;
  ok: number;
  fail: number;
  rate: number;
  share: number; // 占总产出 %
}

export interface StationStat {
  station: string;
  img: number;
  ok: number;
  fail: number;
  rate: number;
  share: number;
  avgLatencyMs: number;
  fallbackRate: number; // %
}

export interface SizeStat {
  size: string;
  img: number;
  share: number;
}

export interface Runtime {
  hasRuntime: boolean; // 运行质量字段是否就绪 (mock=true; 真实 v1 ledger=false)
  avgLatencyMs: number;
  fallbackRate: number; // %
  concEff: number; // 并发效率 (占位字段)
  fallbacks: number;
}

export interface Summary {
  range: RangeKey;
  totalImg: number;
  ok: number;
  fail: number;
  req: number;
  rate: number; // 0..100
  buckets: number;
  line: Record<Line, Bucket>;
  type: { t2i: number; i2i: number; total: number };
  lanes: LaneRow[];
  maxLaneImg: number;
  seriesMode: SeriesMode;
  series: SeriesPoint[];
  peak: number; // 序列峰值
  peakLabel: string;
  models: ModelStat[];
  stations: StationStat[];
  sizes: SizeStat[];
  runtime: Runtime;
  empty: boolean;
}

export function stationOf(r: StatRecord): string {
  return r.station || (r.provider.split("-", 1)[0] || "—");
}

function localDayStart(d: Date): Date {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

// 自适应产出序列: 今日 -> 24 小时时间表; 7天/全部 -> 连续每日 (补空槽, 避免断线)。
function buildSeries(rows: StatRecord[], now: Date, range: RangeKey): { mode: SeriesMode; points: SeriesPoint[] } {
  if (range === "today") {
    const buckets = new Array(24).fill(0);
    for (const r of rows) {
      const d = new Date(r.createdAt);
      if (isNaN(d.getTime())) continue;
      buckets[d.getHours()] += r.imageCount || 0;
    }
    const curH = now.getHours();
    const points = buckets.map((v, h) => ({
      label: String(h).padStart(2, "0"),
      value: v,
      current: h === curH,
      future: h > curH,
    }));
    return { mode: "hour", points };
  }

  // day 模式: 连续天槽位
  const dayVal: Record<string, number> = {};
  let minT = Infinity;
  for (const r of rows) {
    const d = new Date(r.createdAt);
    if (isNaN(d.getTime())) continue;
    const k = `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
    dayVal[k] = (dayVal[k] || 0) + (r.imageCount || 0);
    if (d.getTime() < minT) minT = d.getTime();
  }

  let start: Date;
  if (range === "7d") {
    start = localDayStart(now);
    start.setDate(start.getDate() - 6);
  } else {
    start = isFinite(minT) ? localDayStart(new Date(minT)) : localDayStart(now);
  }

  const end = localDayStart(now);
  const points: SeriesPoint[] = [];
  const cur = new Date(start);
  let guard = 0;
  while (cur.getTime() <= end.getTime() && guard < 366) {
    const k = `${cur.getFullYear()}-${cur.getMonth() + 1}-${cur.getDate()}`;
    points.push({ label: `${cur.getMonth() + 1}/${cur.getDate()}`, value: dayVal[k] || 0 });
    cur.setDate(cur.getDate() + 1);
    guard++;
  }
  return { mode: "day", points };
}

export function summarize(
  records: StatRecord[],
  lineLanes: Record<string, string[]>,
  now: Date,
  range: RangeKey,
): Summary {
  const rows = filterByRange(records, now, range);
  const totalImg = sumField(rows, "imageCount");
  const ok = sumField(rows, "successCount");
  const fail = sumField(rows, "failedCount");
  const req = ok + fail;

  const byLine = groupSum(rows, (r) => r.line);
  const byType = groupSum(rows, (r) => r.requestType);
  const t2i = byType.text_to_image?.imageCount || 0;
  const i2i = byType.image_to_image?.imageCount || 0;

  // lanes: 以 canonical lane 为骨架, 历史/遗留 lane 也补上, 按产出排序。
  const byProvider = groupSum(rows, (r) => r.provider);
  const skeleton: { line: string; provider: string }[] = [];
  for (const line of Object.keys(lineLanes)) {
    for (const p of lineLanes[line]) skeleton.push({ line, provider: p });
  }
  for (const p of Object.keys(byProvider)) {
    if (!skeleton.some((l) => l.provider === p)) {
      const line = rows.find((r) => r.provider === p)?.line || "—";
      skeleton.push({ line, provider: p });
    }
  }
  const lanes: LaneRow[] = skeleton
    .map((l) => ({ ...emptyBucket(), ...byProvider[l.provider], line: l.line, provider: l.provider, index: 0 }))
    .sort((a, b) => b.imageCount - a.imageCount)
    .map((l, i) => ({ ...l, index: i + 1 }));
  const maxLaneImg = lanes.reduce((m, l) => Math.max(m, l.imageCount), 0);

  // 自适应产出序列 (今日按小时 / 7天·全部按天)。
  const { mode: seriesMode, points: series } = buildSeries(rows, now, range);
  let peak = 0;
  let peakLabel = "";
  for (const p of series) {
    if (p.value > peak) {
      peak = p.value;
      peakLabel = p.label;
    }
  }

  // 模型 (= 产线) 概览。
  const mkRate = (b: Bucket) => (b.successCount + b.failedCount > 0 ? Math.round((b.successCount / (b.successCount + b.failedCount)) * 100) : 0);
  const models: ModelStat[] = (["snano", "simage"] as Line[]).map((ln) => {
    const b = byLine[ln] || emptyBucket();
    return {
      line: ln,
      img: b.imageCount,
      ok: b.successCount,
      fail: b.failedCount,
      rate: mkRate(b),
      share: totalImg ? Math.round((b.imageCount / totalImg) * 100) : 0,
    };
  });

  // 中转站记分牌。
  const stationKeys = Array.from(new Set(rows.map(stationOf)));
  const stations: StationStat[] = stationKeys
    .map((st) => {
      const srows = rows.filter((r) => stationOf(r) === st);
      const img = sumField(srows, "imageCount");
      const ok = sumField(srows, "successCount");
      const fail = sumField(srows, "failedCount");
      const req = ok + fail;
      let latW = 0;
      let latN = 0;
      let fb = 0;
      for (const r of srows) {
        if (typeof r.latencyMs === "number") {
          latW += r.latencyMs * (r.successCount || 0);
          latN += r.successCount || 0;
        }
        fb += r.fallbackCount || 0;
      }
      return {
        station: st,
        img,
        ok,
        fail,
        rate: req ? Math.round((ok / req) * 100) : 0,
        share: totalImg ? Math.round((img / totalImg) * 100) : 0,
        avgLatencyMs: latN ? Math.round(latW / latN) : 0,
        fallbackRate: req ? Math.round((fb / req) * 100) : 0,
      };
    })
    .sort((a, b) => b.img - a.img);

  // 分辨率分布。
  const sizeMap: Record<string, number> = {};
  for (const r of rows) {
    if (r.size) sizeMap[r.size] = (sizeMap[r.size] || 0) + (r.imageCount || 0);
  }
  const sizeOrder = ["4K", "3K", "2K", "1K"];
  const sizes: SizeStat[] = Object.keys(sizeMap)
    .sort((a, b) => sizeOrder.indexOf(a) - sizeOrder.indexOf(b))
    .map((sz) => ({ size: sz, img: sizeMap[sz], share: totalImg ? Math.round((sizeMap[sz] / totalImg) * 100) : 0 }));

  // 运行质量汇总。
  const hasRuntime = rows.some((r) => typeof r.latencyMs === "number");
  let latW = 0;
  let latN = 0;
  let fbTotal = 0;
  let concW = 0;
  let concN = 0;
  for (const r of rows) {
    if (typeof r.latencyMs === "number") {
      latW += r.latencyMs * (r.successCount || 0);
      latN += r.successCount || 0;
    }
    fbTotal += r.fallbackCount || 0;
    if (typeof r.concFactor === "number") {
      concW += r.concFactor * (r.successCount || 0);
      concN += r.successCount || 0;
    }
  }
  const runtime: Runtime = {
    hasRuntime,
    avgLatencyMs: latN ? Math.round(latW / latN) : 0,
    fallbackRate: req ? Math.round((fbTotal / req) * 100) : 0,
    concEff: concN ? Math.round((concW / concN) * 10) / 10 : 0,
    fallbacks: fbTotal,
  };

  return {
    range,
    totalImg,
    ok,
    fail,
    req,
    rate: req > 0 ? Math.round((ok / req) * 100) : 0,
    buckets: rows.length,
    line: {
      snano: byLine.snano || emptyBucket(),
      simage: byLine.simage || emptyBucket(),
    },
    type: { t2i, i2i, total: t2i + i2i },
    lanes,
    maxLaneImg,
    seriesMode,
    series,
    peak,
    peakLabel,
    models,
    stations,
    sizes,
    runtime,
    empty: rows.length === 0,
  };
}
