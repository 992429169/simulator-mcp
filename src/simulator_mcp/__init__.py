import asyncio


def main():
    from simulator_mcp.server import main as server_main
    asyncio.run(server_main())
