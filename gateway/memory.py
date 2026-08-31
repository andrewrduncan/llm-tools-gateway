"""
Memory + RAG + file index, backed by Postgres/pgvector.

PARTITION RULE (the important bit):
  writes  -> subject = caller's identity, or NULL when explicitly shared
  reads   -> WHERE subject = :caller OR subject IS NULL
So personal memories stay personal, and NULL-subject rows are visible to everyone.
Everything degrades gracefully: if Postgres is down the LLM still answers, just
without memory.
"""
import os, json
import asyncpg, httpx

DSN   = os.environ.get("PG_DSN", "postgres://llm:llm@postgres:5432/llm")
EMBED = os.environ.get("EMBED_URL", "http://embeddings:8100")
_pool = None


async def pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DSN, min_size=1, max_size=5,
                                          command_timeout=30)
    return _pool


async def embed_text(texts, task="search_document"):
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{EMBED}/embed/text", json={"texts": texts, "task": task})
        r.raise_for_status()
        return r.json()["embeddings"]


async def embed_image_b64(b64s):
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(f"{EMBED}/embed/image", json={"images_b64": b64s})
        r.raise_for_status()
        return r.json()["embeddings"]


def _vec(v):
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


# ------------------------------------------------------------------ memories
async def remember(content, subject=None, source=None, tags=None, shared=False):
    subj = None if shared else subject
    emb = (await embed_text([content]))[0]
    p = await pool()
    row = await p.fetchrow(
        """INSERT INTO memories (subject, content, source, tags, embedding)
           VALUES ($1,$2,$3,$4,$5::vector) RETURNING id, created_at""",
        subj, content, source, tags or [], _vec(emb))
    return {"status": "saved", "id": row["id"],
            "scope": "shared" if subj is None else f"private:{subj}"}


async def recall(query, subject=None, limit=6, min_score=0.35):
    emb = (await embed_text([query], task="search_query"))[0]
    p = await pool()
    rows = await p.fetch(
        """SELECT id, content, subject, source, tags, created_at,
                  1 - (embedding <=> $1::vector) AS score
             FROM memories
            WHERE (subject = $2 OR subject IS NULL)
            ORDER BY embedding <=> $1::vector
            LIMIT $3""",
        _vec(emb), subject, int(limit))
    out = [{"id": r["id"], "content": r["content"],
            "scope": "shared" if r["subject"] is None else "private",
            "score": round(r["score"], 3),
            "when": r["created_at"].strftime("%Y-%m-%d")}
           for r in rows if r["score"] >= min_score]
    return {"query": query, "results": out, "count": len(out)}


async def forget(memory_id=None, query=None, subject=None):
    p = await pool()
    if memory_id is not None:
        # never let one caller delete another's private memory
        n = await p.execute(
            "DELETE FROM memories WHERE id=$1 AND (subject=$2 OR subject IS NULL)",
            int(memory_id), subject)
        return {"deleted": int(n.split()[-1])}
    if not query:
        return {"error": "provide memory_id or query"}
    emb = (await embed_text([query], task="search_query"))[0]
    row = await p.fetchrow(
        """SELECT id, content, 1-(embedding <=> $1::vector) AS score FROM memories
            WHERE (subject=$2 OR subject IS NULL)
            ORDER BY embedding <=> $1::vector LIMIT 1""", _vec(emb), subject)
    if not row or row["score"] < 0.5:
        return {"deleted": 0, "note": "no close match; nothing deleted"}
    await p.execute("DELETE FROM memories WHERE id=$1", row["id"])
    return {"deleted": 1, "id": row["id"], "content": row["content"]}


async def list_memories(subject=None, limit=25):
    p = await pool()
    rows = await p.fetch(
        """SELECT id, content, subject, created_at FROM memories
            WHERE (subject=$1 OR subject IS NULL)
            ORDER BY created_at DESC LIMIT $2""", subject, int(limit))
    return {"count": len(rows), "memories": [
        {"id": r["id"], "content": r["content"][:200],
         "scope": "shared" if r["subject"] is None else "private",
         "when": r["created_at"].strftime("%Y-%m-%d")} for r in rows]}


# ------------------------------------------------------------------ RAG
async def index_document(uri, title, chunks, subject=None):
    embs = await embed_text(chunks)
    p = await pool()
    async with p.acquire() as con:
        await con.execute("DELETE FROM documents WHERE uri=$1", uri)
        await con.executemany(
            """INSERT INTO documents (subject, uri, title, chunk_index, content, embedding)
               VALUES ($1,$2,$3,$4,$5,$6::vector)""",
            [(subject, uri, title, i, c, _vec(e))
             for i, (c, e) in enumerate(zip(chunks, embs))])
    return {"status": "indexed", "uri": uri, "chunks": len(chunks)}


async def search_documents(query, subject=None, limit=5):
    emb = (await embed_text([query], task="search_query"))[0]
    p = await pool()
    rows = await p.fetch(
        """SELECT uri, title, chunk_index, content,
                  1-(embedding <=> $1::vector) AS score
             FROM documents WHERE (subject=$2 OR subject IS NULL)
            ORDER BY embedding <=> $1::vector LIMIT $3""",
        _vec(emb), subject, int(limit))
    return {"query": query, "results": [
        {"uri": r["uri"], "title": r["title"], "chunk": r["chunk_index"],
         "content": r["content"][:800], "score": round(r["score"], 3)}
        for r in rows]}


# ------------------------------------------------------------------ files
async def index_file(bucket, key, url, content_type, size, prompt=None,
                     caption=None, subject=None, image_b64=None, source_file=None):
    emb = None
    if image_b64:
        emb = _vec((await embed_image_b64([image_b64]))[0])
    elif prompt or caption:
        emb = _vec((await embed_text([caption or prompt]))[0])
    p = await pool()
    row = await p.fetchrow(
        """INSERT INTO files (subject,bucket,object_key,url,content_type,bytes,
                              prompt,caption,embedding,source_file)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::vector,$10)
           ON CONFLICT (object_key) DO UPDATE SET url=EXCLUDED.url
           RETURNING id""",
        subject, bucket, key, url, content_type, size, prompt, caption, emb, source_file)
    return {"id": row["id"], "url": url}


async def search_images(query, subject=None, limit=6):
    """Text query -> image results, via the shared text/image embedding space."""
    emb = (await embed_text([query], task="search_query"))[0]
    p = await pool()
    rows = await p.fetch(
        """SELECT url, prompt, caption, created_at,
                  1-(embedding <=> $1::vector) AS score
             FROM files
            WHERE (subject=$2 OR subject IS NULL) AND embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector LIMIT $3""",
        _vec(emb), subject, int(limit))
    return {"query": query, "results": [
        {"url": r["url"], "prompt": r["prompt"], "score": round(r["score"], 3),
         "when": r["created_at"].strftime("%Y-%m-%d"),
         "markdown": f"![{(r['prompt'] or 'image')[:60]}]({r['url']})"}
        for r in rows]}


async def find_file(ref, subject=None, for_write=False):
    """Resolve a url / object_key / bare filename to one indexed file row.

    Read and write deliberately use DIFFERENT partition rules:
      read  -- subject matches OR the row is shared (NULL), same rule as recall
      write -- subject must match EXACTLY. Shared rows stay readable by everyone
               but are deletable only by whoever created them; reusing the read
               rule here would let any caller destroy shared content.
    """
    p = await pool()
    if for_write:
        cond, args = "AND subject IS NOT DISTINCT FROM $2", (ref, subject)
    else:
        cond, args = "AND (subject IS NOT DISTINCT FROM $2 OR subject IS NULL)", (ref, subject)
    return await p.fetchrow(
        f"""SELECT id, object_key, url, source_file, prompt, subject
             FROM files
            WHERE (object_key = $1 OR url = $1 OR url LIKE '%' || $1
                   OR source_file = $1)
              {cond}
            ORDER BY id DESC LIMIT 1""",
        *args)


async def forget_file(row_id):
    p = await pool()
    await p.execute("DELETE FROM files WHERE id = $1", row_id)
