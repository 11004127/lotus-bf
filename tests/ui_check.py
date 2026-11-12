"""Drive the LoTUS-BF UI with Playwright: screenshots + functional checks. Prints JSON results."""
import json
import sys
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000/lotus-bf/"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/home/claude/work/shots"
results = {}
api_calls = []

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1600, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: api_calls.append(r.url) if "/query/" in r.url else None)

    page.goto(BASE)
    page.wait_for_selector(".terms__name")
    page.wait_for_timeout(800)
    page.screenshot(path=f"{OUT}/01_initial.png")

    # 1) typing should NOT trigger studies/nii queries until Enter
    api_calls.clear()
    page.fill(".qb__input", "(pain OR emotion) AND memory")
    page.wait_for_timeout(900)
    results["calls_while_typing"] = sorted({u.split("/")[-1].split("?")[0] for u in api_calls})
    results["interpreted_preview"] = page.inner_text(".qb__status")
    page.press(".qb__input", "Enter")
    page.wait_for_selector(".tablebox__bar b")
    page.wait_for_timeout(1500)
    results["studies_bar"] = page.inner_text(".tablebox__bar span")
    results["study_titles"] = page.locator(".table__title a").all_inner_texts()
    results["url_after_search"] = page.url
    page.screenshot(path=f"{OUT}/02_boolean_query.png")

    # 2) syntax error shown inline
    page.fill(".qb__input", "emotion AND (")
    page.wait_for_timeout(900)
    results["syntax_error_preview"] = page.inner_text(".qb__status")
    page.screenshot(path=f"{OUT}/03_syntax_error.png", clip={"x": 0, "y": 0, "width": 1600, "height": 330})

    # 3) term click with OR, multi-word term quoted
    page.fill(".qb__input", "")
    page.click(".btn--danger")  # reset
    page.fill(".terms input[type=search]", "default")
    page.click(".terms__name >> text=default mode")
    page.wait_for_timeout(300)
    page.click(".seg__btn >> text=OR")
    page.fill(".terms input[type=search]", "orbito")
    page.click(".terms__name >> text=orbitofrontal")
    page.wait_for_timeout(1500)
    results["query_after_term_clicks"] = page.input_value(".qb__input")
    results["studies_after_term_clicks"] = page.inner_text(".tablebox__bar span")

    # 4) related terms
    page.fill(".terms input[type=search]", "emotion")
    page.click(".terms__info >> nth=0")
    page.wait_for_selector(".related .btn--chip")
    results["related_terms"] = page.locator(".related .btn--chip").all_inner_texts()

    # 5) location query + viewer
    page.fill(".qb__input", "[-22,-4,-18] OR memory")
    page.press(".qb__input", "Enter")
    page.wait_for_timeout(2500)
    results["loc_or_term_studies"] = page.inner_text(".tablebox__bar span")
    results["viewer_summary"] = page.inner_text(".viewer__summary")
    page.click(".tabs__btn >> text=Locations")
    page.wait_for_selector(".table--compact tbody tr")
    page.locator(".table--compact tbody tr", has_text="-22").first.click()
    page.wait_for_timeout(500)
    results["cursor_after_row_click"] = [page.input_value(f".coord:nth-child({i}) input") for i in (1, 2, 3)]
    results["value_at_cursor"] = page.inner_text(".viewer__cursor .muted")
    page.screenshot(path=f"{OUT}/04_location_or_term.png")

    # 6) add cursor location to query
    page.click(".viewer__cursor .btn--primary")
    page.wait_for_timeout(300)
    results["query_after_add_location"] = page.input_value(".qb__input")

    # 7) shared URL restores state
    page2 = b.new_page(viewport={"width": 1600, "height": 1000})
    page2.goto(BASE + "?q=reward%20NOT%20emotion&r=6")
    page2.wait_for_selector(".table__title a")
    page2.wait_for_timeout(1200)
    results["shared_url_titles"] = page2.locator(".table__title a").all_inner_texts()

    # 8) narrow screen layout
    page3 = b.new_page(viewport={"width": 420, "height": 900})
    page3.goto(BASE + "?q=emotion")
    page3.wait_for_timeout(2000)
    page3.screenshot(path=f"{OUT}/05_mobile.png", full_page=True)

    # 9) clicking a voxel on the axial canvas → MNI coordinate (expects x=40, y=30)
    box = page.locator(".viewer__slice canvas").nth(2).bounding_box()
    w, h = page.evaluate("() => { const c = document.querySelectorAll('.viewer__slice canvas')[2]; return [c.width, c.height] }")
    s = min(box["width"] / w, box["height"] / h)
    i, j = (90 - 40) // 2, (30 + 126) // 2
    col, row = (91 - 1 - i) + 0.5, (109 - 1 - j) + 0.5
    page.mouse.click(box["x"] + (box["width"] - w * s) / 2 + col * 4 * s, box["y"] + (box["height"] - h * s) / 2 + row * 4 * s)
    page.wait_for_timeout(300)
    results["axial_click_xyz"] = [page.input_value(f".coord:nth-child({k}) input") for k in (1, 2, 3)]

    results["page_errors"] = errors
    b.close()

print(json.dumps(results, ensure_ascii=False, indent=1))
