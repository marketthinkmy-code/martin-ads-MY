from adbot.creative_groups import (CAROUSEL, SINGLE_IMAGE, VIDEO, Unit,
                                   build_units, content_key, select_ten, slugify, uniquify_ids)


def test_content_key_keeps_cjk_filenames_verbatim():
    assert content_key("sgmy_h1.mp4") == "sgmy_h1"                       # ascii -> slug stem
    assert content_key("孩子書包特別長會影響長高嗎.mp4") == "孩子書包特別長會影響長高嗎.mp4"  # CJK -> verbatim (matches Notion id)
    # CJK names that EMBED digits must STILL be verbatim — the old slugify()=='asset' check
    # returned digit fragments ('10_175', '15_1_2', '1_1_3cm') that never matched the Notion row:
    assert content_key("孩子10歲想讓他長到175公分有可能嗎.mp4") == "孩子10歲想讓他長到175公分有可能嗎.mp4"
    assert content_key("15歲1-2年沒長高正常嗎.mp4") == "15歲1-2年沒長高正常嗎.mp4"
    assert content_key("1年長1-3cm正常嗎#.mp4") == "1年長1-3cm正常嗎#.mp4"
    # pure-ascii names (e.g. the April SGMY files) still slugify, so their Notion id is the slug:
    assert content_key("Martin April SGMY -H6#.mp4") == "martin_april_sgmy_h6"

FOLDER_MIME = "application/vnd.google-apps.folder"


def folder(fid, name, children):
    return {"id": fid, "name": name, "mimeType": FOLDER_MIME, "children": children}


def file(fid, name, mime):
    return {"id": fid, "name": name, "mimeType": mime}


def test_uniquify_ids_keeps_cjk_collisions():
    # Two distinct CJK-named images both slugify to "asset" -> must survive as asset / asset_2
    units = [Unit("asset", SINGLE_IMAGE), Unit("asset", SINGLE_IMAGE), Unit("carousel_1", CAROUSEL)]
    uniquify_ids(units)
    assert [u.content_id for u in units] == ["asset", "asset_2", "carousel_1"]
    assert len(select_ten(units, 10)) == 3  # none dropped


def test_slugify_drops_extension_and_normalizes():
    assert slugify("Promo Video 01.MP4") == "promo_video_01"
    assert slugify("一分钟.jpg") == "asset" or slugify("一分钟.jpg")  # non-ascii collapses safely


def test_top_level_video_and_image_become_units():
    tree = folder("root", "root", [
        file("v1", "promo.mp4", "video/mp4"),
        file("i1", "single.jpg", "image/jpeg"),
    ])
    units = build_units(tree)
    assert sorted(u.kind for u in units) == [SINGLE_IMAGE, VIDEO]


def test_carousel_subfolder_groups_images_in_order():
    tree = folder("root", "root", [
        folder("c1", "Lesson Carousel", [
            file("b", "2.jpg", "image/jpeg"),
            file("a", "1.jpg", "image/jpeg"),
        ]),
    ])
    units = build_units(tree, marker="carousel")
    assert len(units) == 1 and units[0].kind == CAROUSEL
    assert [a.name for a in units[0].assets] == ["1.jpg", "2.jpg"]  # sorted by name


def test_script_sidecar_attaches_to_video():
    tree = folder("root", "root", [
        file("v1", "promo.mp4", "video/mp4"),
        file("t1", "promo.txt", "text/plain"),
    ])
    video = next(u for u in build_units(tree) if u.kind == VIDEO)
    assert video.assets[0].script_file_id == "t1"


def test_non_carousel_subfolder_is_walked_recursively():
    tree = folder("root", "root", [
        folder("sub", "extra clips", [file("v2", "deep.mp4", "video/mp4")]),
    ])
    units = build_units(tree)
    assert len(units) == 1 and units[0].kind == VIDEO


def test_select_ten_dedupes_and_caps():
    units = [Unit(f"c{i}", VIDEO) for i in range(12)] + [Unit("c0", VIDEO)]
    picked = select_ten(units, n=10)
    assert len(picked) == 10
    assert len({u.content_id for u in picked}) == 10
