import asyncio

from relay_server import RelayServer


def main() -> None:
    server = RelayServer()
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        print("\n[SERVER] 收到 Ctrl+C，服务已停止")


if __name__ == "__main__":
    main()
