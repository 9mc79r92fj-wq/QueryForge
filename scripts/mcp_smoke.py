import asyncio
import argparse
import shutil

from mcp import (
    ClientSession,
    StdioServerParameters
)

from mcp.client.stdio import stdio_client


async def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("--project")
    parser.add_argument("--env-file")
    parser.add_argument("--query", default="Redis AOF")
    args = parser.parse_args()
    docker = shutil.which("docker")

    if docker is None:

        raise RuntimeError(
            "Docker command not found"
        )

    compose = ["compose"]
    if args.project:
        compose.extend(["-p", args.project])
    if args.env_file:
        compose.extend(["--env-file", args.env_file])

    params = StdioServerParameters(
        command=docker,
        args=compose + ["exec", "-T", "api", "python", "-m", "app.mcp_server"]
    )

    async with stdio_client(
        params
    ) as (read, write):

        async with ClientSession(
            read,
            write
        ) as session:

            async with asyncio.timeout(120):

                initialized = await session.initialize()

                print(
                    "Connected:",
                    initialized.serverInfo.name
                )

                print(
                    "Protocol:",
                    initialized.protocolVersion
                )

                tools = await session.list_tools()

                names = {
                    tool.name
                    for tool in tools.tools
                }

                print("Tools:", sorted(names))

                assert names == {
                    "search_knowledge",
                    "get_document_metadata"
                }

                search_result = await session.call_tool(

                    "search_knowledge",

                    arguments={
                        "query": args.query,
                        "top_k": 3
                    }
                )

                assert not search_result.isError

                data = search_result.structuredContent

                assert data is not None

                print("Search hits:", len(data["hits"]))

                assert data["hits"], (
                    "Knowledge base returned no hits"
                )

                assert not data["degraded"]

                document_id = (
                    data["hits"][0]["document_id"]
                )

                metadata_result = await session.call_tool(

                    "get_document_metadata",

                    arguments={
                        "document_id": document_id
                    }
                )

                assert not metadata_result.isError

                metadata = (
                    metadata_result.structuredContent
                )

                assert metadata is not None

                assert metadata["status"] == "ok"

                assert (
                    metadata["document"]["document_id"]
                    == document_id
                )

                print("Metadata matches queried document")

                invalid_result = await session.call_tool(

                    "search_knowledge",

                    arguments={
                        "query": "Redis",
                        "top_k": 100
                    }
                )

                assert invalid_result.isError

                print("\nInvalid parameter rejected")

                print("\nMCP smoke test: PASSED")


if __name__ == "__main__":

    asyncio.run(main())
