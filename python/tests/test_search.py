import re
import unittest
from unittest.mock import patch

from bson import ObjectId
from fastapi.testclient import TestClient

from main import app
from security.jwt_handler import get_current_user


class Cursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, field, direction):
        self.documents.sort(key=lambda document: document[field], reverse=direction < 0)
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def __aiter__(self):
        return self.iterate()

    async def iterate(self):
        for document in self.documents:
            yield document


class Collection:
    def __init__(self, documents):
        self.documents = documents

    def find(self, query, projection=None):
        matches = []
        for document in self.documents:
            if any(
                re.search(pattern["$regex"], document.get(field) or "", re.IGNORECASE)
                for clause in query["$or"]
                for field, pattern in clause.items()
            ):
                matches.append(
                    {key: value for key, value in document.items() if key == "_id" or key in projection}
                    if projection else dict(document)
                )
        return Cursor(matches)


class GlobalSearchTests(unittest.TestCase):
    def setUp(self):
        self.users = Collection([
            {"_id": ObjectId(), "username": "Gardener", "email": "a@example.com", "role": "user", "password": "secret"},
            {"_id": ObjectId(), "username": "alice", "email": "garden@example.com", "role": "user"},
            {"_id": ObjectId(), "username": "literal.*", "email": "b@example.com", "role": "user"},
        ])
        self.products = Collection([
            {"_id": ObjectId(), "name": "Garden shovel"},
            {"_id": ObjectId(), "name": "Pot", "description": "For the garden"},
            {"_id": ObjectId(), "name": "Seeds", "category": "Gardening"},
            {"_id": ObjectId(), "name": "literal.*"},
        ])
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        app.dependency_overrides[get_current_user] = lambda: {"username": "tester"}
        self.addCleanup(app.dependency_overrides.clear)
        for name, collection in (("users_collection", self.users), ("products_collection", self.products)):
            patcher = patch(f"routes.search.{name}", collection)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_searches_all_fields_case_insensitively_and_hides_passwords(self):
        response = self.client.get("/api/search", params={"query": " GARDEN "})
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(len(result["users"]), 2)
        self.assertEqual(len(result["products"]), 3)
        self.assertEqual(result["users"][0]["id"], str(self.users.documents[0]["_id"]))
        self.assertEqual(set(result["users"][0]), {"id", "username", "email", "role"})
        self.assertEqual(result["products"][0]["name"], "Garden shovel")

    def test_regex_characters_are_literal(self):
        result = self.client.get("/api/search", params={"query": ".*"}).json()
        self.assertEqual([user["username"] for user in result["users"]], ["literal.*"])
        self.assertEqual([product["name"] for product in result["products"]], ["literal.*"])

    def test_no_matches_returns_empty_groups(self):
        response = self.client.get("/api/search", params={"query": "missing"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"users": [], "products": []})

    def test_limit_applies_to_both_groups_in_id_order(self):
        self.users.documents.reverse()
        self.products.documents.reverse()
        result = self.client.get("/api/search", params={"query": "garden", "limit": 1}).json()
        self.assertEqual(len(result["users"]), 1)
        self.assertEqual(len(result["products"]), 1)
        self.assertEqual(result["users"][0]["username"], "Gardener")
        self.assertEqual(result["products"][0]["name"], "Garden shovel")

    def test_invalid_parameters_are_rejected(self):
        for params in ({}, {"query": ""}, {"query": "   "}, {"query": "a" * 201},
                       {"query": "a", "limit": 0}, {"query": "a", "limit": 101},
                       {"query": "a", "limit": "invalid"}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get("/api/search", params=params).status_code, 422)

    def test_authentication_is_required(self):
        app.dependency_overrides.clear()
        response = self.client.get("/api/search", params={"query": "garden"})
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
