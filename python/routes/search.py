import re

from fastapi import APIRouter, Depends, HTTPException, Query

from database import products_collection, users_collection
from security.jwt_handler import get_current_user

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", dependencies=[Depends(get_current_user)])
async def global_search(q: str = Query(...)):
    """Search usernames and products, returning up to five of each.

    Every product query word must occur in its name or description.
    Exact product names rank first; ties are ordered by MongoDB ID.
    """
    q = q.strip()
    if not 1 <= len(q) <= 50:
        raise HTTPException(status_code=422, detail="q must contain 1–50 characters after trimming")

    users = users_collection.find(
        {"username": {"$regex": re.escape(q), "$options": "i"}},
        {"username": 1, "role": 1},
    ).sort("_id", 1).limit(5)

    products = products_collection.aggregate([
        {"$match": {"$and": [
            {"$or": [
                {field: {"$regex": re.escape(word), "$options": "i"}}
                for field in ("name", "description")
            ]}
            for word in q.split()
        ]}},
        {"$addFields": {"_exactName": {"$eq": [
            {"$toLower": {"$ifNull": ["$name", ""]}},
            {"$literal": q.lower()},
        ]}}},
        {"$sort": {"_exactName": -1, "_id": 1}},
        {"$limit": 5},
        {"$project": {"name": 1, "category": 1, "price": 1}},
    ])

    return {
        "users": [
            {"id": str(user["_id"]), "username": user.get("username"), "role": user.get("role")}
            async for user in users
        ],
        "products": [
            {"id": str(product["_id"]), "name": product.get("name"),
             "category": product.get("category"), "price": product.get("price")}
            async for product in products
        ],
    }
