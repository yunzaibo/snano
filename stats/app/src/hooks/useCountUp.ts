import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "framer-motion";

// easeOutExpo 数字滚动: 目标变化时从当前显示值平滑过渡到新目标。
export function useCountUp(target: number, duration = 1.0): number {
  const reduced = useReducedMotion();
  const [value, setValue] = useState(target);
  const displayRef = useRef(target);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    displayRef.current = value;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (reduced) {
      displayRef.current = target;
      setValue(target);
      return;
    }
    const from = displayRef.current;
    const start = performance.now();
    const step = (now: number) => {
      const p = Math.min(1, (now - start) / (duration * 1000));
      const e = p === 1 ? 1 : 1 - Math.pow(2, -10 * p);
      const v = from + (target - from) * e;
      displayRef.current = v;
      setValue(v);
      if (p < 1) rafRef.current = requestAnimationFrame(step);
    };
    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [target, duration, reduced]);

  return value;
}
