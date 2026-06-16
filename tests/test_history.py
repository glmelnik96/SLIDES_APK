import webapp.history as history


def test_add_and_list(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    history.add(id="a", mode="htmlnew", source_filename="x.md",
                result_path=str(tmp_path / "a" / "deck.html"), kind="html")
    history.add(id="b", mode="verstai", source_filename="y.pptx",
                result_path=str(tmp_path / "b" / "out.pptx"), kind="pptx")
    items = history.list_recent()
    assert [i["id"] for i in items] == ["b", "a"]  # newest first


def test_list_caps_at_ten(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    for i in range(12):
        history.add(id=str(i), mode="htmlnew", source_filename=f"{i}.md",
                    result_path="p", kind="html")
    assert len(history.list_recent()) == 10


def test_clear_removes_index_and_files(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    sess = tmp_path / "a"
    sess.mkdir()
    (sess / "deck.html").write_text("hi", encoding="utf-8")
    history.add(id="a", mode="htmlnew", source_filename="x.md",
                result_path=str(sess / "deck.html"), kind="html")
    history.clear()
    assert history.list_recent() == []
    assert not sess.exists()
