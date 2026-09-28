import * as Tabs from "@radix-ui/react-tabs";
import { motion } from "framer-motion";
import { RANGES, type RangeKey } from "../lib/stats";

export function RangeTabs({
  value,
  onChange,
  layoutId = "range-pill",
}: {
  value: RangeKey;
  onChange: (v: RangeKey) => void;
  layoutId?: string;
}) {
  return (
    <Tabs.Root value={value} onValueChange={(v) => onChange(v as RangeKey)}>
      <Tabs.List className="flex items-center gap-1" aria-label="时间范围">
        {RANGES.map((r) => (
          <Tabs.Trigger
            key={r.key}
            value={r.key}
            className="relative px-3.5 py-1.5 font-mono text-[11px] tracking-wide text-paper-faint outline-none transition-colors data-[state=active]:text-paper hover:text-paper-dim"
          >
            {value === r.key && (
              <motion.span
                layoutId={layoutId}
                className="absolute inset-0 rounded-md border border-line bg-ink-700"
                transition={{ type: "spring", stiffness: 420, damping: 34 }}
              />
            )}
            <span className="relative">{r.label}</span>
          </Tabs.Trigger>
        ))}
      </Tabs.List>
    </Tabs.Root>
  );
}
