import { motion } from "framer-motion";
import { useCountUp } from "../hooks/useCountUp";
import { cn, fmt } from "../lib/utils";

const EASE = [0.16, 1, 0.3, 1] as const;

// 动画数字
export function Figure({
  value,
  className,
  suffix,
  duration,
}: {
  value: number;
  className?: string;
  suffix?: string;
  duration?: number;
}) {
  const v = useCountUp(value, duration);
  return (
    <span className={cn("tabular-nums", className)}>
      {fmt(Math.round(v))}
      {suffix}
    </span>
  );
}

// 百分比数字 (0..100)
export function FigurePct({ value, className }: { value: number; className?: string }) {
  const v = useCountUp(value);
  return <span className={cn("tabular-nums", className)}>{Math.round(v)}%</span>;
}

// 油墨条 (生长动画)
export function InkBar({
  pct,
  color,
  className,
  height = 6,
}: {
  pct: number;
  color: string;
  className?: string;
  height?: number;
}) {
  const w = Math.max(0, Math.min(100, pct));
  return (
    <div
      className={cn("w-full overflow-hidden rounded-full bg-line-2", className)}
      style={{ height }}
    >
      <motion.div
        className="h-full rounded-full"
        style={{ background: color }}
        initial={{ width: 0 }}
        animate={{ width: `${w}%` }}
        transition={{ duration: 1, ease: EASE }}
      />
    </div>
  );
}

// 环形仪表 (share / rate), 中心放数字。圆是正方 viewBox 等比缩放, 无非等比 dash 问题。
export function Ring({
  pct,
  color,
  size = 132,
  stroke = 8,
  children,
}: {
  pct: number;
  color: string;
  size?: number;
  stroke?: number;
  children?: React.ReactNode;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const p = Math.max(0, Math.min(100, pct));
  const off = c * (1 - p / 100);
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(236,232,225,0.07)" strokeWidth={stroke} />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          initial={{ strokeDashoffset: c }}
          animate={{ strokeDashoffset: off }}
          transition={{ duration: 1.1, ease: EASE }}
          style={{ filter: `drop-shadow(0 0 6px ${color}55)` }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">{children}</div>
    </div>
  );
}

// 分段 LED 刻度条 (替代单调实心横杠), 给中转站产出更"仪表"的质感。
export function SegMeter({
  pct,
  color,
  segments = 24,
}: {
  pct: number;
  color: string;
  segments?: number;
}) {
  const filled = Math.round((Math.max(0, Math.min(100, pct)) / 100) * segments);
  return (
    <div className="flex h-[14px] w-full items-stretch gap-[2px]">
      {Array.from({ length: segments }).map((_, i) => {
        const on = i < filled;
        const edge = on && i === filled - 1;
        return (
          <span
            key={i}
            className="seg flex-1"
            style={{
              background: on ? color : "rgba(236,232,225,0.06)",
              opacity: on ? (i < filled - 3 ? 0.85 : 1) : 1,
              boxShadow: edge ? `0 0 6px ${color}aa` : undefined,
            }}
          />
        );
      })}
    </div>
  );
}

// 小标题: 中文标题 + 等宽英文 tag
export function Caption({ children, tag }: { children: React.ReactNode; tag?: string }) {
  return (
    <div className="flex items-baseline gap-3">
      {tag && <span className="kicker">{tag}</span>}
      <span className="text-[13px] font-medium text-paper-dim">{children}</span>
    </div>
  );
}
