import re

from fastapi import APIRouter, Depends, HTTPException, Query, status

from database import products_collection, users_collection
from routes.products import product_to_response
from security.jwt_handler import get_current_user

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", dependencies=[Depends(get_current_user)])
async def global_search(
    query: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=100),
):
    """Search usernames/emails and product names/descriptions/categories.

    Matching is case-insensitive and treats the query as literal text.
    The limit applies separately to each result group, ordered by ID.
    """
    query = query.strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Query must contain non-whitespace characters",
        )

    pattern = {"$regex": re.escape(query), "$options": "i"}
    user_cursor = users_collection.find(
        {"$or": [{field: pattern} for field in ("username", "email")]},
        {"username": 1, "email": 1, "role": 1},
    ).sort("_id", 1).limit(limit)
    product_cursor = products_collection.find(
        {"$or": [{field: pattern} for field in ("name", "description", "category")]}
    ).sort("_id", 1).limit(limit)

    users = [
        {
            "id": str(user["_id"]),
            "username": user.get("username"),
            "email": user.get("email"),
            "role": user.get("role"),
        }
        async for user in user_cursor
    ]
    products = [product_to_response(product) async for product in product_cursor]
    return {"users": users, "products": products}
