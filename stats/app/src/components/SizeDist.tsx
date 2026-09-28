import { motion } from "framer-motion";
import { Caption, Figure } from "./primitives";
import { sizeInk } from "../lib/theme";
import type { Summary } from "../lib/stats";

const EASE = [0.16, 1, 0.3, 1] as const;

// 分辨率分布: 比例色阶 (4K 最暖) + 每档读数。区别于其它横杠的视觉语汇。
export function SizeDist({ s }: { s: Summary }) {
  const sizes = s.sizes;
  const total = sizes.reduce((a, x) => a + x.img, 0);
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <Caption tag="Resolution">分辨率分布</Caption>
        <span className="font-mono text-[11px] text-paper-faint">{sizes.length} 档</span>
      </div>

      {total === 0 ? (
        <div className="mt-5 font-mono text-[11px] text-paper-faint">该范围暂无分辨率数据</div>
      ) : (
        <>
          {/* 比例色阶 */}
          <div className="mt-5 flex h-7 w-full gap-[3px] overflow-hidden rounded-[3px]">
            {sizes.map((sz, i) => (
              <motion.div
                key={sz.size}
                className="flex items-center justify-center font-mono text-[10px] font-bold"
                style={{ background: sizeInk(sz.size), color: "rgba(12,11,10,0.82)" }}
                initial={{ flexGrow: 0 }}
                animate={{ flexGrow: Math.max(sz.share, 4) }}
                transition={{ duration: 1, ease: EASE, delay: i * 0.06 }}
              >
                {sz.share >= 9 ? sz.size : ""}
              </motion.div>
            ))}
          </div>
          {/* 读数 */}
          <div className="mt-4 flex flex-wrap gap-x-7 gap-y-2.5">
            {sizes.map((sz) => (
              <div key={sz.size} className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-[2px]" style={{ background: sizeInk(sz.size) }} />
                <span className="font-mono text-[12px] text-paper-dim">{sz.size}</span>
                <Figure value={sz.img} className="font-mono text-[12px] text-paper" />
                <span className="font-mono text-[11px] text-paper-faint">张 · {sz.share}%</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
