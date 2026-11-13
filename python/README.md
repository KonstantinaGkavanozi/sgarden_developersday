# Python API

## Global search

`GET /api/search?query=garden&limit=20` requires an authenticated user's
`Authorization: Bearer <token>` header.

Search matches literal, case-insensitive substrings in user `username` and
`email`, and product `name`, `description`, and `category`. Surrounding query
whitespace is trimmed. Queries must contain 1–200 characters and cannot be
whitespace only; invalid parameters return HTTP 422.

The response is an object with `users` and `products` arrays. User entries
contain `id`, `username`, `email`, and `role`; product entries use the existing
product response format. No matches returns two empty arrays. Each array is
ordered by ID and independently capped by `limit` (default 20, range 1–100).

## Tests

Install `requirements-dev.txt`, then run `python -m unittest discover -s tests -v`.
Search endpoint tests use in-memory collection doubles and do not require MongoDB.
