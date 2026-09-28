import { motion } from "framer-motion";
import { Figure, Caption } from "./primitives";
import { INK } from "../lib/theme";
import { pct } from "../lib/utils";
import type { Summary } from "../lib/stats";

const EASE = [0.16, 1, 0.3, 1] as const;

export function TypeBalance({ s }: { s: Summary }) {
  const { t2i, i2i, total } = s.type;
  const t2iPct = total ? (t2i / total) * 100 : 0;
  const i2iPct = total ? (i2i / total) * 100 : 0;

  return (
    <div>
      <Caption tag="Request Mix">文生图 / 图生图</Caption>
      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 font-mono text-[12px]">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-[2px]" style={{ background: INK.t2i }} />
          <span className="text-paper-dim">文生图 t2i</span>
          <Figure value={t2i} className="text-paper" />
          <span className="text-paper-faint">张 · {pct(t2i, total)}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-paper-faint">{pct(i2i, total)} ·</span>
          <Figure value={i2i} className="text-paper" />
          <span className="text-paper-dim">图生图 i2i</span>
          <span className="h-2 w-2 rounded-[2px]" style={{ background: INK.i2i }} />
        </div>
      </div>
      <div className="mt-3.5 flex h-3 w-full overflow-hidden rounded-full bg-line-2">
        <motion.div
          className="h-full"
          style={{ background: INK.t2i }}
          initial={{ width: 0 }}
          animate={{ width: `${t2iPct}%` }}
          transition={{ duration: 1, ease: EASE }}
        />
        <motion.div
          className="h-full"
          style={{ background: INK.i2i }}
          initial={{ width: 0 }}
          animate={{ width: `${i2iPct}%` }}
          transition={{ duration: 1, ease: EASE, delay: 0.05 }}
        />
      </div>
    </div>
  );
}
