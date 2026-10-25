"""R02-002：archive 档交回的必须是快照正文，不是 Wayback 的 capture 索引页。

现场（2026-10-26 · P02 杜蘅 · S1 · review/round-02）：EUR-Lex 的 GDPR PDF 抓取
失败升级到 archive 档，交回的「内容」是 Wayback 的 capture 列表页（"Wayback
Machine / 978 captures / 11 Mar 2017 - 17 Oct 2026"，482B），content_ok=true、
summary「200 OK」。披露的字段都在（archived_at、pages= 没生效的自白），但章盖
在头上——法务照 content_ok=true 引用，引用回来的是目录。

根因：`_fetch_from_archive` 接受任何 status<400 且非空的快照体，没有索引页形状
检测——archive.org 在快照不可用/重定向时会送回它自己的列表页。

契约（本文件钉住）：
  * 快照体是 capture 索引页形状 → 拒收（返回 None + note 说明），不许以
    content_ok=true 出门；
  * 真快照正文（即使首行碰巧含 "Wayback Machine" 提及）不受影响。
"""

from __future__ import annotations

from dhole_mcp.server import _wayback_capture_index_body

INDEX_BODY = ("Wayback Machine\n\n\n\n[978 captures](/web/20261017063610*/https://eur-lex.example "
              "\"See a list of every capture for this URL\")\n\n11 Mar 2017 - 17 Oct 2026\n\n")


def test_the_capture_listing_shape_is_named():
    assert _wayback_capture_index_body(INDEX_BODY) is True


def test_a_real_snapshot_body_is_not_mistaken_for_the_index():
    real = ("<!doctype html><html><head><title>Regulation (EU) 2016/679</title></head>"
            "<body><h1>Article 1 Subject-matter</h1><p>This Regulation lays down "
            "rules relating to the protection of natural persons...</p>"
            "<p>The Wayback Machine is referenced nowhere.</p></body></html>")
    assert _wayback_capture_index_body(real) is False


def test_an_unrelated_page_is_not_mistaken_either():
    assert _wayback_capture_index_body("<html><body><p>plain text page</p></body></html>") is False
