from __future__ import annotations

import re
import uuid
from typing import Any

from core import server_features


class Adapter:
    async def start(self, manager: Any, instance: dict[str, Any], resume_job_id: str | None = None) -> dict[str, Any]:
        config = dict(instance.get("config") or {})
        keyword = str(config.get("keyword") or "").strip()
        if not keyword:
            raise RuntimeError("Job 4 thiếu Keyword Shopee")
        count = max(1, min(5, int(config.get("product_count") or 5)))
        search = await manager.engine.call(
            "parenting",
            "POST",
            "/api/parenting/shopee/search-preview",
            {"keywords": [keyword], "count": count, "content_pillar": "mixed", "affiliate_id": ""},
            timeout=240,
        )
        raw_items = search.get("items") or search.get("results") or []
        products = []
        seen = set()
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            url = str(item.get("origin_url") or item.get("url") or item.get("product_url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            products.append({
                "id": item.get("id") or item.get("product_id") or url,
                "title": item.get("title") or item.get("name") or item.get("product_title") or keyword,
                "url": url,
                "origin_url": url,
                "affiliate_url": str(item.get("affiliate_url") or server_features.get_affiliate(url) or "").strip(),
                "price": item.get("price") or item.get("price_text") or "",
                "image": item.get("image") or item.get("image_url") or item.get("thumbnail") or "",
                "shopId": item.get("shopId") or item.get("shop_id") or "",
                "itemId": item.get("itemId") or item.get("item_id") or "",
            })
            if len(products) >= count:
                break
        if not products:
            raise RuntimeError(f"Job 4 không tìm thấy sản phẩm Shopee cho keyword: {keyword}")
        missing = [p for p in products if not p.get("affiliate_url")]
        if missing:
            sub_id = re.sub(r"[^a-zA-Z0-9]", "", str(config.get("sub_id") or ""))[:50]
            try:
                res = await manager.flow_broker.request_extension({
                    "type": "SHOPEE_AFFILIATE_CONVERT",
                    "requestId": "job4_aff_" + uuid.uuid4().hex[:12],
                    "links": [p["origin_url"] for p in missing[:5]],
                    "subIds": [sub_id] if sub_id else [],
                }, timeout=180)
            except Exception as exc:
                raise RuntimeError(f"Job 4 đổi affiliate lỗi: {exc}")
            if not res.get("ok"):
                raise RuntimeError(str(res.get("error") or "Job 4 đổi affiliate lỗi"))
            by_origin = {str(row.get("origin_url") or ""): str(row.get("affiliate_url") or "") for row in (res.get("items") or [])}
            for product in products:
                product["affiliate_url"] = product.get("affiliate_url") or by_origin.get(product["origin_url"], "")
                if product.get("affiliate_url"):
                    server_features.set_affiliate(product["origin_url"], product["affiliate_url"], "job4")
        if bool(config.get("affiliate_required", True)):
            bad = [i + 1 for i, p in enumerate(products) if not str(p.get("affiliate_url") or "").strip()]
            if bad:
                raise RuntimeError(f"Job 4 thiếu affiliate_url cho sản phẩm: {bad}")
        run_config = dict(config)
        run_config.update({
            "job_type": "shopee",
            "template_mode": "shopee",
            "shopee_products": products,
            "product_url": products[0]["origin_url"],
            "affiliate_url": products[0].get("affiliate_url") or "",
            "product_video_mode": str(config.get("product_video_mode") or "one_product_per_video"),
        })
        return await manager.engine.run_parenting(run_config, instance["id"], instance["name"])

    async def wait(self, manager: Any, instance: dict[str, Any], started: dict[str, Any]) -> dict[str, Any]:
        return await manager.engine.wait_parenting(started["engine_run_id"])
