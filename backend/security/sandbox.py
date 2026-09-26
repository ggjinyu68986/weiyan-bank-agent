"""进程级沙箱（兜底方案，Docker 双轨之一）。

本系统架构上【不执行 LLM 生成的任意代码】——Agent 只调用白名单银行工具，
工具白名单即"逻辑沙箱"（见技术文档·安全设计第 3 条）。

若未来接入"LLM 写代码"类能力（如交易规则脚本、报表表达式），
统一经本模块执行，保证：受限、可监控、可终止。

用法示例：
    with sandbox_timeout(seconds=3):
        result = run_isolated_python("print(1)", env={}, cwd=tmp)
"""
from __future__ import annotations

import subprocess
import tempfile
import textwrap
import time
from contextlib import contextmanager
from pathlib import Path

# 不允许导入的模块（防逃逸）
BLOCKED_MODULES = {"os", "sys", "subprocess", "socket", "ctypes", "importlib", "pathlib", "shutil"}


@contextmanager
def sandbox_timeout(seconds: float):
    """超时门：超时即抛出，由上层熔断。"""
    if hasattr(signal, "SIGALRM"):
        import signal

        def _handler(*_):
            raise TimeoutError(f"sandbox timeout after {seconds}s")

        signal.signal(signal.SIGALRM, _handler)
        signal.alarm(int(seconds) + 1)
    else:
        # Windows：依赖 subprocess timeout
        start = time.time()

        def _check():
            if time.time() - start > seconds:
                raise TimeoutError(f"sandbox timeout after {seconds}s")

        _check
    try:
        yield
    finally:
        if hasattr(signal, "SIGALRM"):
            import signal

            signal.alarm(0)


def run_isolated_python(code: str, env: dict | None = None, cwd: str | None = None,
                        timeout: float = 5.0) -> dict:
    """在受限子进程中执行 Python 片段：超时 + 无 shell + 阻断模块导入。

    返回 {"ok", "stdout", "stderr", "returncode", "elapsed"}。
    阻断模块导入通过注入 __builtins__.__import__ 代理实现。
    """
    guard = textwrap.dedent(f"""
        import builtins as _b
        _REAL_IMPORT = _b.__import__
        def _guarded(name, *a, **k):
            if name.split('.')[0] in {sorted(BLOCKED_MODULES)!r}:
                raise ImportError(f"blocked module: {{name}}")
            return _REAL_IMPORT(name, *a, **k)
        _b.__import__ = _guarded
    """)
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "script.py"
        script.write_text(guard + "\n" + code, encoding="utf-8")
        start = time.time()
        try:
            p = subprocess.run(
                ["python", str(script)],
                capture_output=True, text=True, timeout=timeout,
                env={"PYTHONIOENCODING": "utf-8", **({} if env is None else env)},
                cwd=cwd or td,
            )
            return {"ok": p.returncode == 0, "stdout": p.stdout, "stderr": p.stderr,
                    "returncode": p.returncode, "elapsed": round(time.time() - start, 3)}
        except subprocess.TimeoutExpired:
            return {"ok": False, "stdout": "", "stderr": "timeout",
                    "returncode": -1, "elapsed": timeout}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "stdout": "", "stderr": str(e), "returncode": -1,
                    "elapsed": round(time.time() - start, 3)}


if __name__ == "__main__":
    # 自检：正常代码可跑、禁入模块被拦
    print(run_isolated_python("print(1 + 1)"))
    print(run_isolated_python("import os"))
    print(run_isolated_python("while True: pass", timeout=1))
