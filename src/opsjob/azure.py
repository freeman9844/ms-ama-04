"""Cosmos·Storage Queue 어댑터. SDK는 지연 import(로컬 venv에 없음)."""

from typing import Any


class QueueSender:
    def __init__(self, endpoint: str, credential: Any, name: str = "applications-start") -> None:
        from azure.storage.queue import QueueClient

        # host.json messageEncoding=none: 원문 JSON 그대로 보낸다(v12 기본은 인코딩 없음).
        self._client = QueueClient(endpoint, name, credential=credential)

    def send(self, body: str) -> None:
        self._client.send_message(body)


class CosmosRuns:
    def __init__(self, endpoint: str, database: str, credential: Any) -> None:
        from azure.cosmos import CosmosClient

        db = CosmosClient(endpoint, credential=credential).get_database_client(database)
        self._container = db.get_container_client("applications")

    def by_run(self, run_id: str) -> list[dict[str, Any]]:
        return list(
            self._container.query_items(
                "SELECT * FROM c WHERE c.run_id = @run",
                parameters=[{"name": "@run", "value": run_id}],
                enable_cross_partition_query=True,
            )
        )
