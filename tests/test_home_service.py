import os
import socket
import unittest
from unittest.mock import AsyncMock, patch

from asyncpg import InvalidPasswordError, TooManyConnectionsError
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError


class HealthcheckTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "ENVIRONMENT": "test",
            "POSTGRES_HOST": "localhost",
            "POSTGRES_USER": "test",
            "POSTGRES_PASSWORD": "test",
            "POSTGRES_DB": "app_test",
        })
        environment.start()
        self.addCleanup(environment.stop)

        from app.app_config import config
        from app.clients.postgres_client import PostgresClient
        from app.services.home_service import HomeService

        self.config = config
        self.client = AsyncMock(spec=PostgresClient)
        self.service = HomeService(self.client)

    async def test_healthy_database(self):
        result = await self.service.healthcheck()
        self.assertEqual(result, {
            "status": "ok",
            "app": self.config.APP_NAME,
            "environment": "test",
            "database": "ok",
        })
        self.client.ping.assert_awaited_once_with()

    async def test_expected_database_failures(self):
        errors = [
            OperationalError("SELECT 1", {}, ConnectionError("Connection lost")),
            InterfaceError("SELECT 1", {}, RuntimeError("Connection closed")),
            SQLAlchemyTimeoutError("Connection pool exhausted"),
            InvalidPasswordError("Authentication failed"),
            TooManyConnectionsError("Too many connections"),
            ConnectionRefusedError("Connection refused"),
            ConnectionResetError("Connection reset"),
            socket.gaierror("Host not found"),
            TimeoutError("Connection timed out"),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.client.ping.side_effect = error
                result = await self.service.healthcheck()
                self.assertEqual(result, {
                    "status": "degraded",
                    "app": self.config.APP_NAME,
                    "environment": "test",
                    "database": "unavailable",
                })

    async def test_unexpected_errors_propagate(self):
        for error in (TypeError("Invalid call"), RuntimeError("Unexpected bug")):
            with self.subTest(error=type(error).__name__):
                self.client.ping.side_effect = error
                with self.assertRaises(type(error)) as raised:
                    await self.service.healthcheck()
                self.assertIs(raised.exception, error)

    def test_health_route_status(self):
        from fastapi.testclient import TestClient

        from app.startup import postgres_client
        from main import app

        with (
            patch.object(postgres_client, "connect", new_callable=AsyncMock),
            patch.object(postgres_client, "close", new_callable=AsyncMock),
            patch.object(postgres_client, "ping", new_callable=AsyncMock) as ping,
            TestClient(app) as client,
        ):
            response = client.get("/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["database"], "ok")

            ping.side_effect = ConnectionRefusedError("Connection refused")
            response = client.get("/health")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["status"], "degraded")
            self.assertEqual(response.json()["database"], "unavailable")


if __name__ == "__main__":
    unittest.main()
