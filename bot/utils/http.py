"""共享的 SSL 上下文 + httpx 客户端工厂

## 为什么需要这个模块（2026-11-11 实测）

httpx 每 new 一个 `AsyncClient` 都会重新加载 certifi 的 CA 证书包：

    实测（本机）
      ssl.create_default_context()                    32 ms
      ssl.create_default_context(cafile=certifi...)  393 ms   ← 读 CA 包是大头
      httpx.AsyncHTTPTransport()                     416 ms   ← 内部就是这么建的
      httpx.AsyncClient(trust_env=False)             386 ms   ← 1 个 transport
      httpx.AsyncClient()                           1522 ms   ← 2~4 个 transport

而 `AsyncHTTPTransport.__init__` **是同步执行的**：建 client 的那一瞬间事件循环整个停住。
项目里有 17 处 `AsyncClient(...)`，每次 HTTP 请求都新建 —— 于是：

    @Kei status 实测 504~1499 ms，事件循环 watchdog 报警 847~5191 ms

py-spy 采样 20 秒，**96.9% 的 CPU 时间**都落在
`create_default_context ← create_ssl_context ← AsyncHTTPTransport.__init__`。

## 解决方式

只缓存 SSL 上下文，**不改调用方的结构**：

    from utils.http import new_client
    async with new_client(timeout=10) as client:   # 和原来一模一样
        ...

复用同一个 `ssl.SSLContext` 之后，构造耗时从 1531 ms 降到 **0 ms**。

## 后续可做（本轮没做）

现在是「每请求一个 client」，仍然每次新建 TCP/TLS 连接（这部分是异步的，不阻塞循环，
但会多花几百毫秒）。要再快可以改成全局复用**一个** `AsyncClient`，
代价是要处理生命周期和代理差异。先解决阻塞问题。
"""

from __future__ import annotations

import ssl
import threading

import certifi
import httpx

_lock = threading.Lock()
_context: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    """进程内共享的 SSL 上下文（首次调用时构建，约 400 ms，之后为 0）"""
    global _context
    if _context is None:
        with _lock:
            if _context is None:
                _context = ssl.create_default_context(cafile=certifi.where())
    return _context


def new_client(**kwargs) -> httpx.AsyncClient:
    """等价于 `httpx.AsyncClient(...)`，但复用已构建的 SSL 上下文

    调用方可以照旧传 `timeout` / `follow_redirects` / `proxy` 等参数；
    如果没有显式给 `verify`，就用共享上下文。
    """
    kwargs.setdefault("verify", ssl_context())
    return httpx.AsyncClient(**kwargs)
