import { useCallback, useRef, useState } from "react";

// 用回调 ref + IntersectionObserver 判断某元素是否已滚出视口顶部。
// 回调 ref 在元素挂载/卸载时触发, 所以 key=range 重挂时会自动重新观察。
export function useScrolledPast(): [(node: Element | null) => void, boolean] {
  const [past, setPast] = useState(false);
  const obs = useRef<IntersectionObserver | null>(null);
  const setNode = useCallback((node: Element | null) => {
    obs.current?.disconnect();
    if (node) {
      obs.current = new IntersectionObserver(
        ([e]) => setPast(!e.isIntersecting),
        { rootMargin: "-4px 0px 0px 0px", threshold: 0 },
      );
      obs.current.observe(node);
    }
  }, []);
  return [setNode, past];
}
