import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发代理：/api → 后端 FastAPI(8000)
//
// 端口与目标都可用环境变量覆盖 —— 自动化验证（探针）必须把前端指到
// **独立的副本库后端**上：早先探针复用到了正在运行的生产后端，
// 结果测试数据写进了生产库（见 .verify_tmp/run_batch.py 的护栏）。
// 默认值不变，日常 `npm run dev` 行为与以前完全一致。
const API_TARGET = process.env.VITE_API_TARGET || 'http://127.0.0.1:8000'
const FE_PORT = Number(process.env.VITE_PORT || 5173)

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: FE_PORT,
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
})
