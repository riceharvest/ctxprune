#!/usr/bin/env python3
"""Deterministic generator of Notion-API-shaped MCP tool outputs (compact JSON, as the Notion MCP server prints them).
Shapes follow the public Notion REST API objects (list / page / block / database / rich_text with annotations).
  python3 tests/gen_notion_fixtures.py   -> tests/fixtures/notion/{search,page,block_children,database_query}.json"""
import json, os, random

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "notion")
R = random.Random(7)
WORDS = "roadmap launch review budget sprint design api onboarding metrics customer retro backlog quarter hiring migration".split()
ANN = lambda **k: {"bold": False, "italic": False, "strikethrough": False, "underline": False, "code": False,
                   "color": "default", **k}


def uid(n):
    h = "%032x" % R.getrandbits(128)
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def sent(n=8):
    return " ".join(R.choice(WORDS) for _ in range(n)).capitalize() + "."


def rt(text, href=None, **ann):
    return {"type": "text", "text": {"content": text, "link": {"url": href} if href else None},
            "annotations": ANN(**ann), "plain_text": text, "href": href}


def rich(n=2):
    parts = [rt(sent(6))]
    if n > 1:
        parts.append(rt(R.choice(WORDS), bold=True))
    if n > 2:
        parts.append(rt(sent(4), "https://example.com/" + R.choice(WORDS), code=True))
    return parts


USER = {"object": "user", "id": "6f2a1c3e-0b7d-4e55-9a10-3c8d2b7e41aa"}
DB = "8c1d5e2f-3a4b-4c6d-9e7f-0a1b2c3d4e5f"
WS = "notion.so/Acme-"
T0 = "2025-09-%02dT%02d:%02d:00.000Z"


def page(i, db=True):
    pid = uid(i)
    t = sent(3)[:-1]
    return {"object": "page", "id": pid, "created_time": T0 % (1 + i % 28, 9, i % 60),
            "last_edited_time": T0 % (1 + i % 28, 15, (i * 7) % 60), "created_by": USER, "last_edited_by": USER,
            "cover": None, "icon": {"type": "emoji", "emoji": "📌"} if i % 3 == 0 else None,
            "parent": {"type": "database_id", "database_id": DB} if db else {"type": "workspace", "workspace": True},
            "archived": False, "in_trash": False,
            "properties": {"Status": {"id": "aB%3D", "type": "select", "select": {"id": uid(1)[:8], "name": R.choice(["Todo", "In progress", "Done"]), "color": R.choice(["blue", "green", "gray"])}},
                           "Owner": {"id": "bC%3E", "type": "people", "people": [dict(USER)]},
                           "Estimate": {"id": "cD%3F", "type": "number", "number": R.randint(1, 13)},
                           "Name": {"id": "title", "type": "title", "title": [rt(t)]}},
            "url": "https://www.notion.so/" + t.replace(" ", "-") + "-" + pid.replace("-", ""), "public_url": None}


def block(i, parent):
    typ = R.choice(["paragraph", "paragraph", "heading_2", "bulleted_list_item", "to_do", "code", "divider"])
    b = {"object": "block", "id": uid(i), "parent": {"type": "page_id", "page_id": parent},
         "created_time": T0 % (1 + i % 28, 9, 0), "last_edited_time": T0 % (1 + i % 28, 10, i % 60),
         "created_by": USER, "last_edited_by": USER, "has_children": False, "archived": False, "in_trash": False, "type": typ}
    if typ == "divider":
        b[typ] = {}
    elif typ == "to_do":
        b[typ] = {"rich_text": rich(1), "checked": i % 2 == 0, "color": "default"}
    elif typ == "code":
        b[typ] = {"caption": [], "rich_text": [rt("def f(x):\n    return x + %d" % i)], "language": "python"}
    elif typ == "heading_2":
        b[typ] = {"rich_text": rich(1), "is_toggleable": False, "color": "default"}
    else:
        b[typ] = {"rich_text": rich(R.choice([1, 2, 3])), "color": "default"}
    return b


def lst(results, extra=None):
    d = {"object": "list", "results": results, "next_cursor": None, "has_more": False}
    d.update(extra or {})
    return d


def dump(name, o):
    with open(os.path.join(OUT, name + ".json"), "w") as f:
        f.write(json.dumps(o, ensure_ascii=False, separators=(",", ":")))


def main():
    os.makedirs(OUT, exist_ok=True)
    res = [page(i, db=i % 4 != 0) for i in range(14)]
    res.insert(5, {"object": "database", "id": DB, "created_time": T0 % (3, 9, 0), "last_edited_time": T0 % (4, 9, 0),
                   "title": [rt("Product backlog")], "description": [], "is_inline": False, "archived": False,
                   "in_trash": False, "parent": {"type": "workspace", "workspace": True},
                   "properties": {"Name": {"id": "title", "name": "Name", "type": "title", "title": {}}},
                   "url": "https://www.notion.so/" + DB.replace("-", "")})
    dump("search", lst(res, {"type": "page_or_database", "page_or_database": {}, "request_id": uid(2)}))
    p = page(99)
    p["properties"]["Name"]["title"] = [rt("Q4 planning")]
    dump("page", p)
    pg = uid(3)
    dump("block_children", lst([block(i, pg) for i in range(40)], {"type": "block", "block": {}, "request_id": uid(4)}))
    dump("database_query", lst([page(i) for i in range(20)], {"type": "page_or_database", "page_or_database": {}, "request_id": uid(5)}))


if __name__ == "__main__":
    main()
