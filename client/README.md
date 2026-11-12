# DocFlow Web

DocFlow 的 Vue 3 前端，提供 PDF / Word 上传、文档解析查看、知识库检索、流式问答与来源追溯，并保留视频工作台。

```bash
npm ci
npm run dev
```

开发服务器默认通过 Vite 代理访问 `http://localhost:9090`。后端地址不同时，在项目根目录 `.env` 中设置 `VITE_DEV_PROXY_TARGET`；前后端分开部署时设置 `VITE_API_BASE_URL`。
