import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { Masthead } from "./components/Masthead";
import { StickyBar } from "./components/StickyBar";
import { Lede } from "./components/Lede";
import { useScrolledPast } from "./hooks/useScrolledPast";
import { Margins } from "./components/Margins";
import { Hero } from "./components/Hero";
import { Lines } from "./components/Lines";
import { TypeBalance } from "./components/TypeBalance";
import { SizeDist } from "./components/SizeDist";
import { RuntimeGauges, StationBoard } from "./components/Runtime";
import { Lanes } from "./components/Lanes";
import { Log } from "./components/Log";
import { summarize, stationOf, FALLBACK_LANES, type StatsData, type RangeKey, type Line } from "./lib/stats";
import { MODEL, stationInk } from "./lib/theme";

// 两个声部之间的分隔标识。
function RegisterHeader({ zh, en }: { zh: string; en: string }) {
  return (
    <div className="mt-16 flex items-center gap-4">
      <div className="flex items-baseline gap-3">
        <span className="font-display text-[15px] font-bold tracking-tight text-paper">{zh}</span>
        <span className="kicker">{en}</span>
      </div>
      <div className="ruler-rule flex-1" />
    </div>
  );
}

function Footer({ data }: { data: StatsData | null }) {
  return (
    <footer className="mt-14 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-5 font-mono text-[10.5px] text-paper-faint">
      <span>
        数据由 <span className="text-paper-dim">stats/aggregate.py</span> 生成 · 契约 image-stats/v1
      </span>
      <span>
        {data?.generatedAt ? `生成于 ${data.generatedAt}` : ""} · 源 {data?.source || "—"}
      </span>
    </footer>
  );
}

const RANGE_KEYS: RangeKey[] = ["today", "7d", "all"];
function initialRange(): RangeKey {
  if (typeof location === "undefined") return "7d";
  const h = location.hash.replace("#", "") as RangeKey;
  return RANGE_KEYS.includes(h) ? h : "7d";
}

// 从 ?line=snano|simage 读取启动聚焦 (技能启动面板时带入)。
function initialLine(): Line | null {
  if (typeof location === "undefined") return null;
  const v = new URLSearchParams(location.search).get("line");
  return v === "snano" || v === "simage" ? v : null;
}

// 下钻聚焦提示条 (聚焦产线 / 站点时显示, 可一键查看全部)。
function FocusBanner({
  line,
  station,
  onClear,
}: {
  line: Line | null;
  station: string | null;
  onClear: () => void;
}) {
  const m = line ? MODEL[line] : null;
  const dot = m ? m.color : station ? stationInk(station) : "#ff9a4d";
  const parts = [
    m ? `${m.name} 产线` : null,
    station ? `${station} 站点` : null,
  ].filter(Boolean);
  return (
    <div className="mt-4 flex items-center gap-3 rounded-md border border-line bg-white/[0.015] px-4 py-2.5">
      <span className="h-2 w-2 rounded-[2px]" style={{ background: dot }} />
      <span className="font-mono text-[11px] text-paper-dim">
        下钻聚焦 <span className="font-bold text-paper">{parts.join(" · ")}</span>
      </span>
      <button
        onClick={onClear}
        className="ml-auto font-mono text-[11px] text-paper-faint underline-offset-2 transition-colors hover:text-paper hover:underline"
      >
        查看全部 →
      </button>
    </div>
  );
}

export default function App() {
  const [data, setData] = useState<StatsData | null>(null);
  const [range, setRange] = useState<RangeKey>(initialRange);
  const [focus, setFocus] = useState<Line | null>(initialLine);
  const [station, setStation] = useState<string | null>(null);
  const [heroRef, scrolledPastHero] = useScrolledPast();
  const live = typeof location !== "undefined" && location.protocol.startsWith("http");

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch(`${import.meta.env.BASE_URL}stats-data.json?t=${Date.now()}`, {
          cache: "no-store",
        });
        const d = await res.json();
        if (alive && d && d.records) setData(d);
      } catch {
        /* 静默: 数据缺失时保持上一次状态 */
      }
    };
    load();
    const id = live ? window.setInterval(load, 15000) : undefined;
    return () => {
      alive = false;
      if (id) clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const now = useMemo(
    () => (data?.generatedAt ? new Date(data.generatedAt) : new Date()),
    [data],
  );
  // 仅按产线过滤 (不含站点) —— 用于记分牌, 让它作选择器时始终列出当前产线的全部站点。
  const lineRecords = useMemo(
    () => (data ? data.records.filter((r) => !focus || r.line === focus) : []),
    [data, focus],
  );
  // 再叠加站点过滤 —— 用于 Hero / 仪表 / 明细 / 日志等下钻后的主体。
  const records = useMemo(
    () => lineRecords.filter((r) => !station || stationOf(r) === station),
    [lineRecords, station],
  );
  const summary = useMemo(
    () => (data ? summarize(records, data.lineLanes || FALLBACK_LANES, now, range) : null),
    [data, records, now, range],
  );
  // 记分牌专用: 用未经站点过滤的列表算站点, 保证所有站点常驻可点。
  const boardSummary = useMemo(
    () => (data ? summarize(lineRecords, data.lineLanes || FALLBACK_LANES, now, range) : null),
    [data, lineRecords, now, range],
  );

  // 感光背光: 背景主光随当前主导产线偏色 (snano 琥珀 / simage 薄荷)。
  const glowRgb = useMemo(() => {
    if (!summary) return "255, 176, 112";
    const top = [...summary.models].sort((a, b) => b.img - a.img)[0];
    return top && top.line === "simage" && top.img > 0 ? "111, 224, 191" : "255, 176, 112";
  }, [summary]);

  return (
    <div className="relative min-h-screen">
      {summary && (
        <StickyBar
          show={scrolledPastHero}
          s={summary}
          range={range}
          onRange={setRange}
          focusLine={focus}
          focusStation={station}
        />
      )}
      <div className="lighttable-glow" style={{ "--glow-rgb": glowRgb } as CSSProperties} />
      <div className="lighttable-grid" />
      <div className="lighttable-sweep" />
      <div className="film-grain" />
      <main className="relative z-10 mx-auto max-w-[1080px] px-7 py-14">
        <Margins range={range} />
        <Masthead source={data?.source || "…"} live={live} range={range} onRange={setRange} />
        <div className="ruler-rule mt-5" />
        {(focus || station) && (
          <FocusBanner
            line={focus}
            station={station}
            onClear={() => {
              setFocus(null);
              setStation(null);
            }}
          />
        )}

        {summary ? (
          <div key={range}>
            {/* 编辑导语: 静止数据也有"编辑视角"的活力 */}
            <div className="develop pt-8" style={{ animationDelay: "0.02s" }}>
              <Lede s={summary} />
            </div>

            {/* ───────── 声部一 · 产出概览 ───────── */}
            <section ref={heroRef} className="develop py-12" style={{ animationDelay: "0.08s" }}>
              <Hero s={summary} />
            </section>
            <section
              className="develop border-t border-line pt-9"
              style={{ animationDelay: "0.17s" }}
            >
              <Lines
                s={summary}
                focus={focus}
                onSelect={(l) => setFocus((cur) => (cur === l ? null : l))}
              />
            </section>
            <section
              className="develop mt-11 grid grid-cols-1 gap-x-12 gap-y-10 border-t border-line pt-9 lg:grid-cols-2"
              style={{ animationDelay: "0.26s" }}
            >
              <TypeBalance s={summary} />
              <SizeDist s={summary} />
            </section>

            {/* ───────── 声部二 · 运行质量 ───────── */}
            <RegisterHeader zh="运行质量" en="Runtime · 成功率 / 速度 / 并发 / 站点" />
            <section className="develop pt-9" style={{ animationDelay: "0.15s" }}>
              <RuntimeGauges s={summary} />
            </section>
            <section
              className="develop mt-11 border-t border-line pt-9"
              style={{ animationDelay: "0.23s" }}
            >
              <StationBoard
                s={boardSummary ?? summary}
                activeStation={station}
                onSelect={(st) => setStation((cur) => (cur === st ? null : st))}
              />
            </section>
            <section
              className="develop mt-11 border-t border-line pt-9"
              style={{ animationDelay: "0.31s" }}
            >
              <Lanes s={summary} />
            </section>
            <section
              className="develop mt-11 border-t border-line pt-9"
              style={{ animationDelay: "0.38s" }}
            >
              <Log records={records} now={now} />
            </section>
          </div>
        ) : (
          <div className="py-32 text-center font-mono text-[12px] text-paper-faint">载入数据…</div>
        )}

        <Footer data={data} />
      </main>
    </div>
  );
}
