import asyncio

from mcctl_agent.runtime import ERROR, RUNNING, ManagedServer, ServerRuntimeManager


class EmptyStdout:
    async def readline(self) -> bytes:
        return b""


class FakeProcess:
    def __init__(self, returncode: int | None) -> None:
        self.stdout = EmptyStdout()
        self.returncode = returncode

    async def wait(self) -> int:
        return self.returncode


def test_old_reader_does_not_clobber_new_runtime_status() -> None:
    async def run() -> None:
        manager = ServerRuntimeManager()
        old_process = FakeProcess(returncode=1)
        new_process = FakeProcess(returncode=None)
        server = ManagedServer(server_id="server-1", process=new_process, status=RUNNING)

        await manager._read_console(server, old_process)  # noqa: SLF001 - regression coverage for reader ownership

        assert server.process is new_process
        assert server.status == RUNNING
        assert server.status != ERROR

    asyncio.run(run())
