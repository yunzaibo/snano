// 点墨色板 (与 tailwind.config.js 对应), 供内联 style/SVG 使用。
export const INK: Record<string, string> = {
  snano: "#ff9a4d",
  simage: "#6fe0bf",
  t2i: "#e8c06a",
  i2i: "#d08a5c",
};

export function lineInk(line: string): string {
  return INK[line] || "rgba(236,232,225,0.40)";
}

export const LINE_LABEL: Record<string, string> = {
  snano: "SNANO 产线",
  simage: "SIMAGE 产线",
};

// 产线 -> 模型显示名 / 厂家 (由 SKILL 默认 provider 归一; 站点命名细微差异在前端无关)。
export const MODEL: Record<string, { name: string; vendor: string; color: string }> = {
  snano: { name: "Nano Banana Pro", vendor: "Google", color: "#ff9a4d" },
  simage: { name: "GPT-Image", vendor: "OpenAI", color: "#6fe0bf" },
};

// 中转站身份色: 不写死站点清单。已知站点给稳定锚色 (仅为视觉连续性, 非白名单);
// 未知/新增站点用名称哈希落到暖色板 —— 任何中转站都会拿到稳定、可区分、不含蓝紫的颜色。
const STATION_PALETTE = [
  "#ff9a4d", // amber
  "#e8c06a", // flax
  "#c97f5d", // terracotta
  "#7fb8a0", // sage
  "#d4a23c", // gold
  "#b6745a", // clay
  "#9bbf8a", // leaf
  "#caa66b", // sand
];
const STATION_HINT: Record<string, string> = {
  apiyi: "#e8c06a",
  laozhang: "#c97f5d",
  apimart: "#7fb8a0",
};
function hashIndex(s: string, mod: number): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h % mod;
}
export function stationInk(s: string): string {
  if (!s || s === "—") return "rgba(236,232,225,0.40)";
  return STATION_HINT[s] || STATION_PALETTE[hashIndex(s, STATION_PALETTE.length)];
}
// 兼容旧引用。
export const STATION_INK = STATION_HINT;

// 分辨率梯度色 (4K 最暖最亮; 常见 1K/2K/4K 三档)。
export const SIZE_INK: Record<string, string> = {
  "4K": "#ff9a4d",
  "2K": "#e8c06a",
  "1K": "#7fb8a0",
  "3K": "#d08a5c",
};
export function sizeInk(s: string): string {
  return SIZE_INK[s] || "rgba(236,232,225,0.40)";
}
