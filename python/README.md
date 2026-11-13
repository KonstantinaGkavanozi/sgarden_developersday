# Python API

## Global search

Send `GET /api/search?q=red%20clay` with `Authorization: Bearer <token>`.
Missing, invalid, or expired tokens return 401.

`q` must contain 1–50 characters after trimming; invalid or missing queries
return 422. Matching is case-insensitive and regex characters are literal.
Users match by username. Products match only when every query word occurs in
the name or description, including across both fields. Exact product-name
matches rank first, followed by ID order, before the five-result limit.

The response contains `users` and `products` arrays, each with at most five
entries. Users contain `id`, `username`, `role`. Products contain `id`, `name`,
`category`, `price`. No matches returns empty arrays.

## Tests

Install `requirements-dev.txt`, then run:

```powershell
python -m unittest discover -s tests -v
```

Tests use collection doubles and do not require a running MongoDB server.
