// 值班记录数据层: 把非敏感聚合字段"翻译"成人话事件 + 生成脱敏的「复制给 AI」文本。
// 绝不含 key / 请求地址 / 原始响应。错误类别复用路由 ProviderErrorCategory。
import { MODEL } from "./theme";
import { stationOf, type StatRecord } from "./stats";

export type EventLevel = "fail" | "warn" | "ok";

export interface LogEvent {
  level: EventLevel;
  at: number; // ms
  line: string;
  station: string;
  model: string;
  requestType: string; // t2i | i2i
  size?: string;
  imageCount: number;
  failedCount: number;
  fallbackCount: number;
  errorKind?: string;
  text: string;
}

// 路由错误类别 -> 人话 + 修复方向 (与 router/core/health.py 的 ProviderErrorCategory 对齐)。
export const KIND: Record<string, { label: string; hint: string }> = {
  auth: { label: "认证失败", hint: "key 或 请求地址·模型名 未配对" },
  forbidden: { label: "被拒", hint: "账号无权限或被 Cloudflare 拦" },
  rate_limit: { label: "限流·额度", hint: "短时请求过多或额度用尽" },
  timeout: { label: "超时", hint: "出图或网络较慢" },
  upstream_5xx: { label: "中转站故障", hint: "上游 5xx，通常稍后自行恢复" },
  connection: { label: "连不上", hint: "网络或 DNS 问题" },
  unsupported: { label: "不支持该请求", hint: "该站点不吃此 size 或 i2i" },
  no_image: { label: "没返回图", hint: "调用成功但空结果" },
  invalid_artifact: { label: "出图不达标", hint: "未过 2K/4K 硬门槛" },
  parse_error: { label: "解析失败", hint: "返回格式异常" },
  async_pending: { label: "异步未完成", hint: "仍在出图，需轮询结果" },
  unknown: { label: "未归类", hint: "需查看路由侧" },
};

const typeLabel = (t: string) => (t === "image_to_image" ? "图生图 i2i" : "文生图 t2i");

export function relTime(at: number, now: number): string {
  const m = Math.max(0, Math.round((now - at) / 60000));
  if (m < 1) return "刚刚";
  if (m < 60) return `${m} 分钟前`;
  if (m < 1440) return `${Math.floor(m / 60)} 小时前`;
  return `${Math.floor(m / 1440)} 天前`;
}

// 每个聚合桶翻成一条事件; 按时间倒序。
export function deriveEvents(records: StatRecord[]): LogEvent[] {
  const evs: LogEvent[] = [];
  for (const r of records) {
    const at = new Date(r.createdAt).getTime();
    if (isNaN(at)) continue;
    const model = MODEL[r.line]?.name ?? r.line;
    const station = stationOf(r);
    const failed = r.failedCount || 0;
    const fb = r.fallbackCount || 0;
    const base = {
      at, line: r.line, station, model, requestType: r.requestType,
      size: r.size, imageCount: r.imageCount || 0, failedCount: failed, fallbackCount: fb,
      errorKind: r.errorKind,
    };
    if (failed > 0) {
      evs.push({ ...base, level: "fail",
        text: `${model} ${failed} 张最终失败${fb > 0 ? "（已回退仍失败）" : "（已自动重试）"}` });
    } else if (fb > 0) {
      evs.push({ ...base, level: "warn",
        text: `${model} ${fb} 张回退到备用站点后成功` });
    } else if ((r.successCount || 0) > 0) {
      evs.push({ ...base, level: "ok",
        text: `${model} 出图 ${r.imageCount || 0} 张，全部成功` });
    }
  }
  return evs.sort((a, b) => b.at - a.at);
}

// 脱敏的「复制给 AI」报告: 一次点击即自足, 不需要用户再拼装。
export function buildPayload(e: LogEvent, now: number): string {
  const req = [e.station, typeLabel(e.requestType), e.size].filter(Boolean).join(" · ");
  const k = e.errorKind ? KIND[e.errorKind] : undefined;
  const kindLine = e.errorKind
    ? `错误类别：${e.errorKind} · ${k?.label ?? "异常"}${k?.hint ? `（${k.hint}）` : ""}`
    : "错误类别：未提供（待路由侧补充脱敏类别）";
  return [
    "【显影台·问题报告】（已脱敏：无密钥/地址/原始响应）",
    `产线/模型：${e.line} · ${e.model}`,
    `站点/请求：${req}`,
    `现象：${e.text}`,
    kindLine,
    `时间：${relTime(e.at, now)}`,
    "> 请判断原因并给出修复步骤（优先查：配置/密钥/模型名）。",
  ].join("\n");
}
