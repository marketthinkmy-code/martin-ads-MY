from adbot.creative_groups import (CAROUSEL, SINGLE_IMAGE, VIDEO, Unit,
                                   build_units, select_ten, slugify)

FOLDER_MIME = "application/vnd.google-apps.folder"


def folder(fid, name, children):
    return {"id": fid, "name": name, "mimeType": FOLDER_MIME, "children": children}


def file(fid, name, mime):
    return {"id": fid, "name": name, "mimeType": mime}


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
