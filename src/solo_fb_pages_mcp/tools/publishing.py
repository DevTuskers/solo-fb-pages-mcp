"""D2. Publishing tools: text/link posts, photos, multi-photo, video, reels, list/update/delete/hide."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..context import ToolContext, check_post_belongs, handle_errors, parse_schedule, parse_time, permalink
from ..graph import GraphError, GraphRequest
from ..media import Media, load_media
from ..storage import Page

WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
IDEMPOTENT_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

POST_FIELDS = (
    "id,message,created_time,permalink_url,is_hidden,is_published,status_type,"
    "attachments{media_type,type,url,title,media{image{src}}},"
    "reactions.summary(total_count).limit(0),comments.summary(total_count).limit(0),shares"
)


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    errors = handle_errors(lambda: ctx.redactor)
    g = ctx.graph

    def media(value: str, kind: str, filename: str | None = None) -> Media:
        return load_media(value, kind=kind, allow_local_files=ctx.settings.allow_local_files, filename=filename)

    async def summarize(p: Page, tok: str, post_id: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"post_id": post_id, "permalink": permalink(post_id)}
        try:
            info = await g.get(post_id, token=tok, params={"fields": "created_time,permalink_url"})
            out["created_time"] = info.get("created_time")
            if info.get("permalink_url"):
                out["permalink"] = info["permalink_url"]
        except GraphError:
            pass
        out.update(extra or {})
        return out

    # ---- create ------------------------------------------------------------------------------------------

    @mcp.tool(annotations=WRITE)
    @errors
    async def create_post(
        page: str,
        message: str,
        link: str | None = None,
        scheduled_time: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Publish a text post (optionally with a link preview) to a Page's feed.

        scheduled_time: ISO-8601 or unix seconds, 10 minutes to 30 days ahead; the post is then created
        unpublished and Facebook publishes it at that time. dry_run=true returns the exact request without sending."""
        p = ctx.resolve(page)
        ctx.guard_write()
        ts = parse_schedule(scheduled_time)
        args = {"page": page, "message": message, "link": link, "scheduled_time": scheduled_time}
        data: dict[str, Any] = {"message": message, "link": link}
        if ts:
            data.update(published=False, scheduled_publish_time=ts)
        tok = ctx.token(p)
        req = g.build("POST", f"{p.page_id}/feed", token=tok, data=data)
        if dry_run:
            return ctx.dry_run("create_post", p, args, [req])

        async def run() -> dict[str, Any]:
            res = await g.send(req)
            return await summarize(p, tok, res["id"], {"scheduled_publish_time": ts} if ts else None)

        return await ctx.write("create_post", p, args, run)

    @mcp.tool(annotations=WRITE)
    @errors
    async def create_photo_post(
        page: str,
        image: str,
        caption: str | None = None,
        scheduled_time: str | None = None,
        filename: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Publish a single photo with an optional caption.

        image: a public http(s) URL (preferred), a data: URI or raw base64 of the file. scheduled_time: ISO-8601 or unix seconds (10 min to 30 days ahead).
        Returns the photo id and the feed post_id."""
        p = ctx.resolve(page)
        ctx.guard_write()
        ts = parse_schedule(scheduled_time)
        m = media(image, "image", filename)
        args = {"page": page, "image": image, "caption": caption, "scheduled_time": scheduled_time}
        data: dict[str, Any] = {"message": caption}
        if ts:
            data.update(published=False, scheduled_publish_time=ts)
        tok = ctx.token(p)
        if m.is_url:
            data["url"] = m.url
            req = g.build("POST", f"{p.page_id}/photos", token=tok, data=data)
        else:
            req = g.build("POST", f"{p.page_id}/photos", token=tok, data=data, files={"source": m.as_file()})
        if dry_run:
            return ctx.dry_run("create_photo_post", p, args, [req])

        async def run() -> dict[str, Any]:
            res = await g.send(req)
            post_id = res.get("post_id") or f"{p.page_id}_{res['id']}"
            extra: dict[str, Any] = {"photo_id": res.get("id")}
            if ts:
                extra["scheduled_publish_time"] = ts
            return await summarize(p, tok, post_id, extra)

        return await ctx.write("create_photo_post", p, args, run)

    @mcp.tool(annotations=WRITE)
    @errors
    async def create_multi_photo_post(
        page: str,
        images: list[str],
        message: str = "",
        scheduled_time: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Publish one feed post containing several photos (2-10 recommended).

        Each item in images is a public http(s) URL (preferred), a data: URI or raw base64 of the file. Photos are uploaded unpublished first,
        then attached to one feed post. Unpublished uploads expire after about 24 h."""
        p = ctx.resolve(page)
        ctx.guard_write()
        if not images:
            raise ValueError("images must contain at least one image")
        ts = parse_schedule(scheduled_time)
        items = [media(v, "image", f"image{i}") for i, v in enumerate(images)]
        args = {"page": page, "images": images, "message": message, "scheduled_time": scheduled_time}
        tok = ctx.token(p)

        def photo_req(m: Media) -> GraphRequest:
            data: dict[str, Any] = {"published": False}
            if ts:
                data["temporary"] = True
            if m.is_url:
                data["url"] = m.url
                return g.build("POST", f"{p.page_id}/photos", token=tok, data=data)
            return g.build("POST", f"{p.page_id}/photos", token=tok, data=data, files={"source": m.as_file()})

        def feed_req(media_ids: list[str]) -> GraphRequest:
            data: dict[str, Any] = {"message": message}
            for i, mid in enumerate(media_ids):
                data[f"attached_media[{i}]"] = {"media_fbid": mid}
            if ts:
                data.update(published=False, scheduled_publish_time=ts)
            return g.build("POST", f"{p.page_id}/feed", token=tok, data=data)

        photo_reqs = [photo_req(m) for m in items]
        if dry_run:
            placeholders = [f"<photo_id_{i}>" for i in range(len(items))]
            return ctx.dry_run("create_multi_photo_post", p, args, [*photo_reqs, feed_req(placeholders)])

        async def run() -> dict[str, Any]:
            ids = [str((await g.send(r))["id"]) for r in photo_reqs]
            res = await g.send(feed_req(ids))
            extra: dict[str, Any] = {"photo_ids": ids}
            if ts:
                extra["scheduled_publish_time"] = ts
            return await summarize(p, tok, res["id"], extra)

        return await ctx.write("create_multi_photo_post", p, args, run)

    @mcp.tool(annotations=WRITE)
    @errors
    async def create_video_post(
        page: str,
        video: str,
        description: str = "",
        title: str | None = None,
        scheduled_time: str | None = None,
        filename: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Publish a (non-reel) video to the Page.

        video: a public http(s) URL (preferred), a data: URI or raw base64 of the file. A public file_url is strongly preferred for large files.
        Facebook processes the video asynchronously; poll get_video_status with the returned video_id."""
        p = ctx.resolve(page)
        ctx.guard_write()
        ts = parse_schedule(scheduled_time)
        m = media(video, "video", filename)
        args = {"page": page, "video": video, "title": title, "description": description, "scheduled_time": scheduled_time}
        data: dict[str, Any] = {"title": title, "description": description}
        if ts:
            data.update(published=False, scheduled_publish_time=ts)
        tok = ctx.token(p)
        if m.is_url:
            data["file_url"] = m.url
            req = g.build("POST", f"{p.page_id}/videos", token=tok, data=data, host="video")
        else:
            req = g.build("POST", f"{p.page_id}/videos", token=tok, data=data, files={"source": m.as_file()}, host="video")
        if dry_run:
            return ctx.dry_run("create_video_post", p, args, [req])

        async def run() -> dict[str, Any]:
            res = await g.send(req)
            vid = str(res["id"])
            out: dict[str, Any] = {"video_id": vid, "permalink": permalink(vid), "processing": True}
            if res.get("post_id"):
                out["post_id"] = res["post_id"]
            if ts:
                out["scheduled_publish_time"] = ts
            return out

        return await ctx.write("create_video_post", p, args, run)

    @mcp.tool(annotations=READ)
    @errors
    async def get_video_status(page: str, video_id: str) -> dict[str, Any]:
        """Get processing/publishing status of a video or reel (uploading, processing, ready, error)."""
        p = ctx.resolve(page)
        tok = ctx.token(p)
        return await ctx.read(
            p, lambda: g.get(video_id, token=tok, params={"fields": "id,status,permalink_url,created_time,title,description"})
        )

    @mcp.tool(annotations=WRITE)
    @errors
    async def create_reel(
        page: str,
        video: str,
        description: str = "",
        filename: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Publish a Facebook Reel (vertical video, 3-90 seconds). Limit: 30 API-published reels per Page per 24 h.

        video: a public http(s) URL (preferred), a data: URI or raw base64 of the file. Runs start -> upload (rupload.facebook.com) -> finish."""
        p = ctx.resolve(page)
        ctx.guard_write()
        m = media(video, "video", filename)
        args = {"page": page, "video": video, "description": description}
        tok = ctx.token(p)
        start = g.build("POST", f"{p.page_id}/video_reels", token=tok, data={"upload_phase": "start"})

        def upload_req(video_id: str, upload_url: str | None) -> GraphRequest:
            path, host = (upload_url, "absolute") if upload_url else (video_id, "rupload")
            if m.is_url:
                return g.build("POST", path, token=tok, host=host, headers={"file_url": str(m.url)}, auth_scheme="OAuth")  # type: ignore[arg-type]
            assert m.content is not None
            return g.build(
                "POST",
                path,
                token=tok,
                host=host,  # type: ignore[arg-type]
                headers={"offset": "0", "file_size": str(len(m.content)), "Content-Type": "application/octet-stream"},
                content=m.content,
                auth_scheme="OAuth",
            )

        def finish_req(video_id: str) -> GraphRequest:
            return g.build(
                "POST",
                f"{p.page_id}/video_reels",
                token=tok,
                data={"upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED", "description": description},
            )

        if dry_run:
            return ctx.dry_run("create_reel", p, args, [start, upload_req("<video_id>", None), finish_req("<video_id>")])

        async def run() -> dict[str, Any]:
            s = await g.send(start)
            vid = str(s["video_id"])
            up = await g.send(upload_req(vid, s.get("upload_url")))
            if up.get("success") is False:
                raise ValueError(f"reel upload failed: {up}")
            fin = await g.send(finish_req(vid))
            out: dict[str, Any] = {"video_id": vid, "permalink": permalink(vid), "success": fin.get("success", True)}
            if fin.get("post_id"):
                out["post_id"] = fin["post_id"]
            return out

        return await ctx.write("create_reel", p, args, run)

    # ---- read --------------------------------------------------------------------------------------------

    @mcp.tool(annotations=READ)
    @errors
    async def list_posts(page: str, limit: int = 10, since: str | None = None) -> dict[str, Any]:
        """List the Page's own published posts, newest first, with reaction/comment counts and hidden state.

        since: ISO-8601 or unix seconds."""
        p = ctx.resolve(page)
        tok = ctx.token(p)
        params: dict[str, Any] = {"fields": POST_FIELDS, "limit": max(1, min(limit, 100)), "since": parse_time(since)}
        res = await ctx.read(p, lambda: g.get(f"{p.page_id}/published_posts", token=tok, params=params))
        return {"posts": [_post(x) for x in res.get("data", [])]}

    @mcp.tool(annotations=READ)
    @errors
    async def list_scheduled_posts(page: str) -> dict[str, Any]:
        """List posts scheduled for future publication."""
        p = ctx.resolve(page)
        tok = ctx.token(p)
        res = await ctx.read(
            p,
            lambda: g.get(
                f"{p.page_id}/scheduled_posts",
                token=tok,
                params={"fields": "id,message,scheduled_publish_time,created_time,permalink_url"},
            ),
        )
        return {"posts": res.get("data", [])}

    # ---- modify ------------------------------------------------------------------------------------------

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    @errors
    async def update_post(page: str, post_id: str, message: str, dry_run: bool = False) -> dict[str, Any]:
        """Edit the text of a post. Only works for posts created by this app."""
        p = ctx.resolve(page)
        ctx.guard_write()
        check_post_belongs(p, post_id)
        tok = ctx.token(p)
        req = g.build("POST", post_id, token=tok, data={"message": message})
        args = {"page": page, "post_id": post_id, "message": message}
        if dry_run:
            return ctx.dry_run("update_post", p, args, [req])

        async def run() -> dict[str, Any]:
            res = await g.send(req)
            return {"post_id": post_id, "success": res.get("success", True), "permalink": permalink(post_id)}

        return await ctx.write("update_post", p, args, run)

    @mcp.tool(annotations=DESTRUCTIVE)
    @errors
    async def delete_post(page: str, post_id: str, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        """Permanently delete a post. Destructive: requires confirm=true. Consider hide_post instead."""
        p = ctx.resolve(page)
        ctx.guard_write(destructive=True, confirm=confirm, dry_run=dry_run)
        check_post_belongs(p, post_id)
        tok = ctx.token(p)
        req = g.build("DELETE", post_id, token=tok)
        args = {"page": page, "post_id": post_id}
        if dry_run:
            return ctx.dry_run("delete_post", p, args, [req])

        async def run() -> dict[str, Any]:
            res = await g.send(req)
            return {"post_id": post_id, "deleted": bool(res.get("success", True))}

        return await ctx.write("delete_post", p, args, run)

    async def set_post_hidden(tool: str, page: str, post_id: str, hidden: bool, dry_run: bool) -> dict[str, Any]:
        p = ctx.resolve(page)
        ctx.guard_write()
        check_post_belongs(p, post_id)
        tok = ctx.token(p)
        req = g.build("POST", post_id, token=tok, data={"is_hidden": hidden})
        fallback = g.build("POST", post_id, token=tok, data={"timeline_visibility": "hidden" if hidden else "normal"})
        args = {"page": page, "post_id": post_id}
        if dry_run:
            out = ctx.dry_run(tool, p, args, [req])
            out["fallback_on_error"] = fallback.preview(ctx.redactor)
            return out

        async def run() -> dict[str, Any]:
            try:
                res = await g.send(req)
                method = "is_hidden"
            except GraphError as err:
                if err.code not in (1, 100):
                    raise
                res = await g.send(fallback)
                method = "timeline_visibility"
            return {"post_id": post_id, "is_hidden": hidden, "success": res.get("success", True), "method": method}

        return await ctx.write(tool, p, args, run)

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    @errors
    async def hide_post(page: str, post_id: str, dry_run: bool = False) -> dict[str, Any]:
        """Hide a Page post from the Page timeline (reversible with unhide_post)."""
        return await set_post_hidden("hide_post", page, post_id, True, dry_run)

    @mcp.tool(annotations=IDEMPOTENT_WRITE)
    @errors
    async def unhide_post(page: str, post_id: str, dry_run: bool = False) -> dict[str, Any]:
        """Unhide a previously hidden Page post."""
        return await set_post_hidden("unhide_post", page, post_id, False, dry_run)


def _post(x: dict[str, Any]) -> dict[str, Any]:
    atts = [
        {
            k: v
            for k, v in {
                "type": a.get("type") or a.get("media_type"),
                "url": a.get("url"),
                "title": a.get("title"),
                "image": (a.get("media") or {}).get("image", {}).get("src"),
            }.items()
            if v
        }
        for a in (x.get("attachments") or {}).get("data", [])
    ]
    return {
        "id": x.get("id"),
        "message": x.get("message"),
        "created_time": x.get("created_time"),
        "permalink": x.get("permalink_url") or permalink(str(x.get("id"))),
        "is_hidden": x.get("is_hidden"),
        "is_published": x.get("is_published"),
        "attachments": atts,
        "reactions": (x.get("reactions") or {}).get("summary", {}).get("total_count"),
        "comments": (x.get("comments") or {}).get("summary", {}).get("total_count"),
        "shares": (x.get("shares") or {}).get("count", 0),
    }
