import { useMemo, useState } from "react";
import { Caption } from "./primitives";
import { deriveEvents, buildPayload, relTime, type LogEvent, KIND } from "../lib/log";
import type { StatRecord } from "../lib/stats";

const LV: Record<string, string> = { fail: "#d0654a", warn: "#e8a24d", ok: "#9bbf8a" };

function copyText(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  return new Promise((res, rej) => {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); res(); } catch (e) { rej(e); } finally { ta.remove(); }
  });
}

function Dot({ level }: { level: string }) {
  const c = LV[level] || "rgba(236,232,225,0.34)";
  return (
    <span
      className="mt-[6px] h-[7px] w-[7px] flex-none rounded-full"
      style={{ background: c, boxShadow: `0 0 7px ${c}99` }}
    />
  );
}

function Row({ e, now, onCopy }: { e: LogEvent; now: number; onCopy: (e: LogEvent) => void }) {
  const can = e.level === "fail" || e.level === "warn";
  const k = e.errorKind ? KIND[e.errorKind] : undefined;
  return (
    <div className="flex items-baseline gap-3.5 border-t border-line py-2.5">
      <Dot level={e.level} />
      <span className="w-[62px] flex-none font-mono text-[11px] leading-snug text-paper-faint">
        {relTime(e.at, now)}
      </span>
      <span className="min-w-0 flex-1 font-sans text-[13px] leading-relaxed text-paper-dim">
        {e.station && <b className="font-semibold text-paper">{e.station}</b>}
        {e.station && "｜"}
        {e.text}
        {e.level === "fail" && k && (
          <span className="mt-1 block text-[11.5px] text-paper-faint">↳ {k.label}：{k.hint}</span>
        )}
      </span>
      {can && (
        <button
          onClick={() => onCopy(e)}
          className="flex-none self-center whitespace-nowrap rounded-md border border-line bg-[rgba(255,154,77,0.06)] px-2.5 py-1.5 font-mono text-[11px] text-paper-dim transition-colors hover:border-[rgba(255,154,77,0.5)] hover:bg-[rgba(255,154,77,0.12)] hover:text-paper active:scale-[0.96]"
        >
          复制给 AI
        </button>
      )}
    </div>
  );
}

export function Log({ records, now }: { records: StatRecord[]; now: Date }) {
  const nowMs = now.getTime();
  const events = useMemo(() => deriveEvents(records), [records]);
  const [toast, setToast] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [histAll, setHistAll] = useState(false);

  const anomalies = events.filter((e) => e.level !== "ok");
  const hasFail = anomalies.some((e) => e.level === "fail");
  const hasWarn = anomalies.some((e) => e.level === "warn");
  const pill = hasFail
    ? { c: LV.fail, t: "有失败待关注" }
    : hasWarn
      ? { c: LV.warn, t: "有回退·降速" }
      : { c: LV.ok, t: "全线正常" };

  const onCopy = (e: LogEvent) => {
    copyText(buildPayload(e, nowMs)).then(
      () => { setToast("已复制问题报告，可直接粘贴给 AI"); setTimeout(() => setToast(null), 1800); },
      () => { setToast("复制失败，请手动选择"); setTimeout(() => setToast(null), 1800); },
    );
  };

  const histPool = histAll ? events : anomalies;

  return (
    <div>
      <div className="flex items-baseline justify-between">
        <Caption tag="Darkroom Log">值班记录 · 只报异常</Caption>
        <span className="font-mono text-[11px] text-paper-faint">{events.length} 条</span>
      </div>

      {/* 状态药丸 */}
      <div className="mt-4 inline-flex items-center gap-2.5 rounded-full border border-line px-4 py-2 font-sans text-[12px] font-semibold text-paper">
        <span className="h-2 w-2 rounded-full" style={{ background: pill.c, boxShadow: `0 0 8px ${pill.c}88` }} />
        当前状态 · {pill.t}
        <span className="font-normal text-paper-faint">
          本范围{anomalies.length ? ` · ${anomalies.length} 条需留意` : "无异常 · 待命中"}
        </span>
      </div>

      {/* 最近异常 (常驻、不折叠) */}
      <div className="mt-3">
        {anomalies.length === 0 ? (
          <div className="border-t border-line py-3 font-sans text-[13px] text-paper-faint">
            🟢 本范围运转平稳，最近一批全部成功。
          </div>
        ) : (
          anomalies.slice(0, 6).map((e, i) => <Row key={i} e={e} now={nowMs} onCopy={onCopy} />)
        )}
      </div>

      {/* 历史入口 (独立记录页, 非就地展开) */}
      <div className="mt-3 flex items-center gap-3">
        <button
          onClick={() => setOpen(true)}
          className="font-mono text-[12px] text-paper-faint underline-offset-2 transition-colors hover:text-paper hover:underline"
        >
          查看全部记录 ({events.length}) →
        </button>
      </div>
      <div className="mt-3 font-mono text-[10.5px] leading-relaxed text-paper-faint opacity-80">
        复制内容已隐去密钥与敏感地址 · 数据来自非敏感聚合字段，不读 .env / 原始日志
      </div>

      {/* 全部记录模态 */}
      {open && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 backdrop-blur-sm sm:items-center" onClick={() => setOpen(false)}>
          <div
            className="max-h-[80vh] w-full max-w-[640px] overflow-hidden rounded-xl border border-line bg-[#100e0b] shadow-2xl"
            onClick={(ev) => ev.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-line px-5 py-4">
              <Caption tag="Records">全部值班记录</Caption>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setHistAll(false)}
                  className={`rounded-full px-3 py-1 font-sans text-[11px] font-semibold transition-colors ${!histAll ? "bg-[rgba(255,154,77,0.16)] text-paper" : "text-paper-faint hover:text-paper"}`}
                >
                  仅异常
                </button>
                <button
                  onClick={() => setHistAll(true)}
                  className={`rounded-full px-3 py-1 font-sans text-[11px] font-semibold transition-colors ${histAll ? "bg-[rgba(255,154,77,0.16)] text-paper" : "text-paper-faint hover:text-paper"}`}
                >
                  全部
                </button>
                <button onClick={() => setOpen(false)} className="ml-1 font-mono text-[13px] text-paper-faint hover:text-paper">✕</button>
              </div>
            </div>
            <div className="max-h-[64vh] overflow-y-auto px-5 py-2">
              {histPool.length === 0 ? (
                <div className="py-6 font-sans text-[13px] text-paper-faint">该范围暂无记录</div>
              ) : (
                histPool.map((e, i) => <Row key={i} e={e} now={nowMs} onCopy={onCopy} />)
              )}
            </div>
          </div>
        </div>
      )}

      {/* toast */}
      {toast && (
        <div className="fixed bottom-8 left-1/2 z-[60] -translate-x-1/2 rounded-lg border border-[rgba(255,154,77,0.4)] bg-[#1a1713] px-5 py-3 font-sans text-[13px] font-semibold text-paper shadow-2xl">
          {toast}
        </div>
      )}
    </div>
  );
}
