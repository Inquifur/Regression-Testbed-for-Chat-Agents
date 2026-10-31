"""独立离线进程的业务网络隔离；失败尝试也留证据。"""
import asyncio
from contextlib import ExitStack
import socket
from unittest.mock import patch


def run_offline(coroutine):
    attempts = []

    def deny(operation):
        def blocked(*args, **kwargs):
            attempts.append(operation)
            raise RuntimeError(f"离线运行禁止网络操作：{operation}")
        return blocked

    # 只允许事件循环初始化的内部 socketpair，不允许业务连接。
    with asyncio.Runner() as runner:
        runner.get_loop()
        with ExitStack() as stack:
            for target, names in ((socket.socket, ("connect", "connect_ex", "sendto")),
                                  (socket, ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"))):
                for name in names:
                    stack.enter_context(patch.object(target, name, deny(name)))
            result = runner.run(coroutine)
    if attempts:
        result["errors"].append({"kind": "network_attempt", "path": "offline_guard",
                                 "message": "业务曾尝试网络操作，即使已被业务代码捕获也不能通过",
                                 "operations": attempts})
        result["status"] = "error"
    return result
