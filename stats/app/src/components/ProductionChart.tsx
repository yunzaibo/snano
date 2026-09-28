import { motion } from "framer-motion";
import { useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Caption } from "./primitives";
import type { SeriesMode, SeriesPoint } from "../lib/stats";

const AMBER = "#ff9a4d";
const AMBER_HI = "#ffb877";

// 测量容器实际像素宽度: 让折线在真实像素坐标里绘制, 避免 viewBox 非等比拉伸
// 造成的「描边盖不满 / 圆点被压扁」。
function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(el.clientWidth);
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0]?.contentRect;
      if (cr) setW(cr.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

// 是否开启了"减少动态"偏好 (用于关闭装饰性常驻动画, 守红线)。
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useLayoutEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const on = () => setReduced(mq.matches);
    on();
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return reduced;
}

// 今日: 24 小时产出时间表 (柱状)。柱高为静态值, 入场仅做位移, 数据恒可见。
function HourTimeline({ points, peak }: { points: SeriesPoint[]; peak: number }) {
  const max = peak || 1;
  return (
    <div className="h-[92px] w-full">
      <div className="flex h-full items-end gap-[3px]">
        {points.map((p, i) => {
          const h = p.value > 0 ? Math.max(3, (p.value / max) * 100) : 0;
          const tick = i % 6 === 0; // 0/6/12/18 标刻度
          return (
            <div key={p.label} className="group relative flex h-full flex-1 flex-col justify-end">
              {/* 轨道底 */}
              <div className="absolute inset-x-0 bottom-0 top-0 rounded-[2px] bg-line-2" />
              {p.value > 0 && (
                <div
                  className="relative rounded-[2px] colrise"
                  style={{
                    height: `${h}%`,
                    background: p.current ? AMBER_HI : AMBER,
                    opacity: p.future ? 0.35 : 1,
                    boxShadow: p.current ? `0 0 10px ${AMBER}66` : undefined,
                    animationDelay: `${Math.min(i * 18, 480)}ms`,
                  }}
                  title={`${p.label}:00 · ${p.value} 张`}
                />
              )}
              {p.current && (
                <span className="absolute -top-1 left-1/2 h-1 w-1 -translate-x-1/2 rounded-full bg-amber-bright shadow-[0_0_6px_#ffb877]" />
              )}
              {tick && (
                <span className="pointer-events-none absolute -bottom-[18px] left-0 font-mono text-[9px] text-paper-faint">
                  {p.label}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// 7天/全部: 面积折线 (真实像素坐标)。底层静态线永远完整可见; 顶层扫线为糖衣;
// 默认克制 (只露峰值/首尾 + 一条峰值基准线给"大概"量感), 悬停某天才浮出该日 ≈ 数值。
function DayArea({ points, peak }: { points: SeriesPoint[]; peak: number }) {
  const [wrapRef, w] = useElementWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);
  const reduced = usePrefersReducedMotion();

  const H = 112;
  const padX = 7;
  const padTop = 20; // 顶部给悬停浮签 + 峰值留白
  const labelH = 18; // 底部日期带
  const plotBottom = H - labelH;
  const max = peak || 1;
  const n = points.length;
  const W = w || 320;
  const innerW = Math.max(W - padX * 2, 1);

  const xs = (i: number) => (n <= 1 ? W / 2 : padX + (innerW * i) / (n - 1));
  const ys = (v: number) => padTop + (plotBottom - padTop) * (1 - v / max);

  const linePts = points.map((p, i) => `${xs(i)},${ys(p.value)}`).join(" ");
  const dPath = points.map((p, i) => `${i === 0 ? "M" : "L"} ${xs(i)},${ys(p.value)}`).join(" ");
  const areaPts = `${padX},${plotBottom} ${linePts} ${W - padX},${plotBottom}`;
  const peakIdx = points.reduce((m, p, i) => (p.value > points[m].value ? i : m), 0);

  // 点少时每点一枚淡点描出节奏; 点多时只留峰值/末点, 不糊成一片。
  const showAllDots = n <= 16;
  // 底部日期: 少则全显, 多则只显首 / 峰 / 尾。
  const labelIdx = n <= 8 ? points.map((_, i) => i) : Array.from(new Set([0, peakIdx, n - 1]));

  const onHover = (e: ReactPointerEvent<SVGRectElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = innerW <= 0 ? 0 : (e.clientX - rect.left - padX) / innerW;
    setActive(Math.max(0, Math.min(n - 1, Math.round(ratio * (n - 1)))));
  };

  const act = active != null ? points[active] : null;
  const ax = active != null ? xs(active) : 0;

  return (
    <div ref={wrapRef} className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} className="block overflow-visible">
        <defs>
          <linearGradient id="yieldFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={AMBER} stopOpacity="0.20" />
            <stop offset="100%" stopColor={AMBER} stopOpacity="0" />
          </linearGradient>
          <filter id="beadGlow" x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur stdDeviation="2.4" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* 峰值水平基准线: 给"大概"的纵向量感, 不标精确刻度 */}
        <line
          x1={padX}
          y1={ys(peak)}
          x2={W - padX}
          y2={ys(peak)}
          stroke="rgba(236,232,225,0.10)"
          strokeWidth="1"
          strokeDasharray="2 4"
        />
        {/* 底基线 */}
        <line x1={padX} y1={plotBottom} x2={W - padX} y2={plotBottom} stroke="rgba(236,232,225,0.12)" strokeWidth="1" />

        <polygon points={areaPts} fill="url(#yieldFill)" />

        {/* 静态完整线 (始终可见, 暗琥珀) —— 即便动画不跑数据也不丢 */}
        <polyline
          points={linePts}
          fill="none"
          stroke={AMBER}
          strokeWidth="1.6"
          strokeLinejoin="round"
          strokeLinecap="round"
          strokeOpacity="0.5"
        />
        {/* 扫线糖衣 (像素坐标 -> 完整盖满右侧) */}
        <motion.polyline
          points={linePts}
          fill="none"
          stroke={AMBER}
          strokeWidth="1.6"
          strokeLinejoin="round"
          strokeLinecap="round"
          initial={{ pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: 1.1, ease: [0.16, 1, 0.3, 1] }}
        />

        {/* 每点淡标 (点少时) */}
        {showAllDots &&
          points.map((p, i) =>
            i === peakIdx || i === n - 1 ? null : (
              <circle key={p.label} cx={xs(i)} cy={ys(p.value)} r="1.8" fill={AMBER} fillOpacity={p.value > 0 ? 0.5 : 0.18} />
            ),
          )}

        {/* 峰值点 */}
        <circle cx={xs(peakIdx)} cy={ys(points[peakIdx].value)} r="2.6" fill={AMBER_HI} />
        {/* 末点呼吸 */}
        <motion.circle
          cx={xs(n - 1)}
          cy={ys(points[n - 1].value)}
          r="2.6"
          fill={AMBER_HI}
          animate={{ opacity: [1, 0.3, 1] }}
          transition={{ duration: 2.2, repeat: Infinity, ease: "easeInOut" }}
        />

        {/* 生产脉冲: 发光珠子沿折线持续巡游 —— 数据静止也始终在动 (呼应"产线") */}
        {!reduced && n > 1 && (
          <circle r="3" fill={AMBER_HI} filter="url(#beadGlow)" opacity="0.9">
            <animateMotion dur="3.8s" repeatCount="indefinite" path={dPath} />
          </circle>
        )}

        {/* 悬停: 竖向导引 + 实心点 */}
        {act && (
          <g>
            <line x1={ax} y1={padTop - 8} x2={ax} y2={plotBottom} stroke="rgba(236,232,225,0.22)" strokeWidth="1" strokeDasharray="2 3" />
            <circle cx={ax} cy={ys(act.value)} r="3.4" fill={AMBER_HI} stroke="#0c0b0a" strokeWidth="1.5" />
          </g>
        )}

        {/* 底部日期带 */}
        {labelIdx.map((i) => {
          const anchor = i === 0 ? "start" : i === n - 1 ? "end" : "middle";
          const x = i === 0 ? padX : i === n - 1 ? W - padX : xs(i);
          return (
            <text
              key={`l${i}`}
              x={x}
              y={H - 5}
              textAnchor={anchor}
              className="font-mono"
              fill={i === peakIdx ? AMBER : "rgba(236,232,225,0.34)"}
              fontSize="9"
            >
              {points[i].label}
            </text>
          );
        })}

        {/* 透明捕获层 (只覆盖绘图区, 不挡日期带) */}
        <rect
          x={0}
          y={0}
          width={W}
          height={plotBottom}
          fill="transparent"
          style={{ cursor: "crosshair" }}
          onPointerMove={onHover}
          onPointerLeave={() => setActive(null)}
        />
      </svg>

      {/* 悬停浮签 (HTML 覆盖; 近似读数, 用 ≈ 暗示"大概") */}
      {act && (
        <div
          className="pointer-events-none absolute top-0 -translate-x-1/2 whitespace-nowrap rounded-md border border-line bg-[#0c0b0a]/90 px-2 py-1 font-mono text-[10px] leading-tight backdrop-blur"
          style={{ left: Math.min(Math.max(ax, 28), W - 28) }}
        >
          <span className="text-paper-dim">{act.label}</span>
          <span className="mx-1 text-paper-faint">·</span>
          <span className="text-paper">≈ {act.value}</span>
          <span className="ml-0.5 text-paper-faint">张</span>
        </div>
      )}
    </div>
  );
}

export function ProductionChart({
  mode,
  series,
  peak,
  peakLabel,
}: {
  mode: SeriesMode;
  series: SeriesPoint[];
  peak: number;
  peakLabel: string;
}) {
  const hasData = peak > 0;
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <Caption tag={mode === "hour" ? "Today · Hourly" : "Yield"}>
          {mode === "hour" ? "今日产出时间表" : "按天产出"}
        </Caption>
        {hasData && (
          <span className="font-mono text-[11px] text-paper-faint">
            峰值 <span className="text-paper-dim">{peak}</span>
            <span className="ml-1 text-paper-faint">@ {peakLabel}{mode === "hour" ? ":00" : ""}</span>
          </span>
        )}
      </div>
      <div className={mode === "hour" ? "mt-3 pb-5" : "mt-3"}>
        {!hasData ? (
          <div className="flex h-[92px] items-center font-mono text-[11px] text-paper-faint">
            {mode === "hour" ? "今日尚无产出 · 等待第一张" : "数据点不足"}
          </div>
        ) : mode === "hour" ? (
          <HourTimeline points={series} peak={peak} />
        ) : (
          <DayArea points={series} peak={peak} />
        )}
      </div>
    </div>
  );
}
