# 微言 · AI 银行智能体 —— Docker 沙箱（双轨之一）
# 赛题"沙箱环境：生成的代码在受限、可监控的逻辑隔离环境中运行"：
#   - 逻辑沙箱：Agent 只调用白名单银行工具，不执行 LLM 生成的任意代码；
#   - 进程沙箱：backend/security/sandbox.py（正常/禁入/超时三态验证）；
#   - 容器沙箱（本文件）：整个系统跑在隔离容器内，资源受限、日志可监控。
#
# 构建：docker build -t weiyan-bank-agent .
# 运行：docker run -d -p 8000:8000 --name weiyan-agent --memory=512m --cpus=1 weiyan-bank-agent
# 验证：docker logs -f weiyan-agent   （审计/错误全部走 stdout，可监控）

FROM python:3.12-slim

WORKDIR /app

# 依赖层单独缓存（改代码不重装依赖）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码（不含 .env/.git/测试报告缓存，见 .dockerignore）
COPY . .

# 默认 MockLLM 即可完整演示（无外部 LLM 依赖）
# 如需真实模型：docker run -e LLM_API_KEY=sk-xxx -e LLM_BASE_URL=... -e LLM_MODEL=deepseek-chat ...

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')" || exit 1

CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
