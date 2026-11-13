import re
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from bson import ObjectId
from fastapi.testclient import TestClient
from jose import jwt

from main import app
from security.jwt_handler import ALGORITHM, SECRET_KEY, create_token


def matches(document, query):
    if "$and" in query:
        return all(matches(document, clause) for clause in query["$and"])
    if "$or" in query:
        return any(matches(document, clause) for clause in query["$or"])
    return all(re.search(pattern["$regex"], document.get(field) or "", re.I)
               for field, pattern in query.items())


def evaluate(expression, document):
    if isinstance(expression, str) and expression.startswith("$"):
        return document.get(expression[1:])
    if not isinstance(expression, dict):
        return expression
    operator, value = next(iter(expression.items()))
    if operator == "$literal":
        return value
    if operator == "$ifNull":
        result = evaluate(value[0], document)
        return result if result is not None else evaluate(value[1], document)
    if operator == "$toLower":
        return evaluate(value, document).lower()
    if operator == "$eq":
        return evaluate(value[0], document) == evaluate(value[1], document)
    raise AssertionError(f"Unexpected expression: {expression}")


class Cursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, field, direction):
        self.documents.sort(key=lambda doc: doc[field], reverse=direction < 0)
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    async def __aiter__(self):
        for document in self.documents:
            yield document


class Collection:
    def __init__(self, documents):
        self.documents = documents

    def find(self, query, projection):
        return Cursor([{key: value for key, value in doc.items()
                        if key == "_id" or projection.get(key)}
                       for doc in self.documents if matches(doc, query)])

    def aggregate(self, pipeline):
        documents = [dict(doc) for doc in self.documents]
        for stage in pipeline:
            operator, value = next(iter(stage.items()))
            if operator == "$match":
                documents = [doc for doc in documents if matches(doc, value)]
            elif operator == "$addFields":
                for doc in documents:
                    doc.update({key: evaluate(expression, doc) for key, expression in value.items()})
            elif operator == "$sort":
                for key, direction in reversed(list(value.items())):
                    documents.sort(key=lambda doc: doc[key], reverse=direction < 0)
            elif operator == "$limit":
                documents = documents[:value]
            elif operator == "$project":
                documents = [{key: item for key, item in doc.items()
                              if key == "_id" or value.get(key)} for doc in documents]
            else:
                raise AssertionError(f"Unexpected stage: {stage}")
        return Cursor(documents)


class GlobalSearchTests(unittest.TestCase):
    def setUp(self):
        self.users = Collection([])
        self.products = Collection([])
        self.auth_lookup = AsyncMock(return_value={"_id": ObjectId(), "username": "tester"})
        for target, value in (("routes.search.users_collection", self.users),
                              ("routes.search.products_collection", self.products),
                              ("security.jwt_handler.users_collection.find_one", self.auth_lookup)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        token = create_token(str(ObjectId()), "tester", "user")
        self.headers = {"Authorization": f"Bearer {token}"}

    def search(self, q):
        return self.client.get("/api/search", params={"q": q}, headers=self.headers)

    def test_missing_invalid_and_expired_tokens_return_401(self):
        payloads = [{}, {"sub": "invalid"}, {"sub": None}, {"sub": 123},
                    {"sub": str(ObjectId()), "exp": datetime.utcnow() - timedelta(hours=1)}]
        tokens = ["bad-token", jwt.encode({"sub": str(ObjectId())}, "wrong-secret", algorithm=ALGORITHM),
                  *[jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM) for payload in payloads]]
        for authorization in [None, "Basic abc", *[f"Bearer {token}" for token in tokens]]:
            with self.subTest(authorization=authorization):
                headers = {"Authorization": authorization} if authorization else {}
                self.assertEqual(self.client.get("/api/search?q=pot", headers=headers).status_code, 401)
        self.auth_lookup.assert_not_awaited()

    def test_deleted_user_returns_401(self):
        self.auth_lookup.return_value = None
        self.assertEqual(self.search("pot").status_code, 401)

    def test_validation_happens_after_trimming(self):
        for q in ("", " \t\n ", "a" * 51):
            with self.subTest(q=q):
                self.assertEqual(self.search(q).status_code, 422)
        for q in (" a ", " " + "a" * 50 + " "):
            self.assertEqual(self.search(q).status_code, 200)
        self.assertEqual(self.client.get("/api/search", headers=self.headers).status_code, 422)

    def test_fields_limits_and_username_only_matching(self):
        self.users.documents = [
            {"_id": ObjectId(), "username": f"PotUser{i}", "role": "user", "email": "secret", "password": "hash"}
            for i in range(7)
        ]
        self.users.documents.insert(0, {"_id": ObjectId(), "username": "alice", "email": "pot"})
        self.products.documents = [
            {"_id": ObjectId(), "name": f"Pot {i}", "description": "secret", "category": "garden", "price": 3}
            for i in range(7)
        ]
        response = self.search(" pOt ")
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(set(result), {"users", "products"})
        self.assertEqual(len(result["users"]), 5)
        self.assertEqual(len(result["products"]), 5)
        for user in result["users"]:
            self.assertEqual(set(user), {"id", "username", "role"})
            self.assertTrue(ObjectId.is_valid(user["id"]))
            self.assertTrue(user["username"].startswith("PotUser"))
        for product in result["products"]:
            self.assertEqual(set(product), {"id", "name", "category", "price"})
            self.assertTrue(ObjectId.is_valid(product["id"]))

    def test_every_word_can_match_across_fields_and_exact_name_ranks_before_limit(self):
        self.products.documents = [
            {"_id": ObjectId(), "name": f"Red pot {i}", "description": "clay"} for i in range(6)
        ]
        exact = {"_id": ObjectId(), "name": "RED CLAY"}
        self.products.documents.extend([
            exact,
            {"_id": ObjectId(), "name": "Red", "description": "plastic"},
            {"_id": ObjectId(), "name": "Clay", "category": "red"},
        ])
        result = self.search("red clay").json()["products"]
        self.assertEqual(len(result), 5)
        self.assertEqual(result[0]["id"], str(exact["_id"]))
        self.assertTrue(all(item["name"].startswith("Red pot") for item in result[1:]))
        # Reverse order also matches, and repeated whitespace separates words.
        self.assertEqual(len(self.search("clay   red").json()["products"]), 5)

    def test_regex_characters_are_literal(self):
        for q in (".*", "[", "$name", "a+b"):
            with self.subTest(q=q):
                self.users.documents = [{"_id": ObjectId(), "username": q}, {"_id": ObjectId(), "username": "other"}]
                self.products.documents = [{"_id": ObjectId(), "name": q}, {"_id": ObjectId(), "name": "other"}]
                response = self.search(q)
                self.assertEqual(response.status_code, 200)
                self.assertEqual([user["username"] for user in response.json()["users"]], [q])
                self.assertEqual([product["name"] for product in response.json()["products"]], [q])

    def test_all_product_words_are_required_in_searchable_fields(self):
        self.products.documents = [
            {"_id": ObjectId(), "name": "Red pot", "description": "CLAY"},
            {"_id": ObjectId(), "name": "Pot", "description": "red clay"},
            {"_id": ObjectId(), "name": "Red", "description": None},
            {"_id": ObjectId(), "name": "Clay", "category": "red"},
        ]
        result = self.search("red clay").json()["products"]
        self.assertEqual([product["name"] for product in result], ["Red pot", "Pot"])

    def test_no_matches_and_existing_endpoint(self):
        self.assertEqual(self.search("missing").json(), {"users": [], "products": []})
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
