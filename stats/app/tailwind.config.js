/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 暖墨基底 (warm ink, 非蓝黑)
        ink: { 900: "#0c0b0a", 800: "#13110d", 700: "#1d1a14", 600: "#2a251d" },
        // 骨白文字
        paper: { DEFAULT: "#ece8e1", dim: "#a39d92", faint: "#6b655c" },
        line: "rgba(236,232,225,0.10)",
        "line-2": "rgba(236,232,225,0.055)",
        // 两条产线的点墨实色
        amber: { DEFAULT: "#ff9a4d", bright: "#ffb877", deep: "#c9763a" },
        mint: { DEFAULT: "#6fe0bf", bright: "#9defce", deep: "#3fae90" },
        // 请求类型 (暖调亚麻/陶土)
        flax: "#e8c06a",
        clay: "#d08a5c",
        // 状态
        sage: "#9bbf8a",
        brick: "#d2674e",
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'PingFang SC', 'Microsoft YaHei', 'sans-serif'],
        display: ['"Space Grotesk"', 'Inter', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      letterSpacing: { tightest: '-0.04em' },
      keyframes: {
        grainshift: {
          '0%,100%': { transform: 'translate(0,0)' },
          '10%': { transform: 'translate(-3%,-4%)' },
          '30%': { transform: 'translate(2%,-2%)' },
          '50%': { transform: 'translate(-1%,3%)' },
          '70%': { transform: 'translate(3%,1%)' },
          '90%': { transform: 'translate(-2%,2%)' },
        },
      },
      animation: { grain: 'grainshift 8s steps(4) infinite' },
    },
  },
  plugins: [],
};
