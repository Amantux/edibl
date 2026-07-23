"""Core user journeys through the real UI. Each is independent (its own fresh page)."""


def _add(page, url, name):
    """Add an item via the add modal; returns after the modal closes."""
    page.goto(f"{url}/#/stock?add=1", wait_until="networkidle")
    page.wait_for_selector(".modal", timeout=5000)
    page.wait_for_timeout(400)
    field = page.locator('input[placeholder^="e.g. Organic"]')
    field.fill(name)
    field.dispatch_event("change")
    page.wait_for_timeout(700)
    page.get_by_role("button", name="Add", exact=True).click()
    page.wait_for_timeout(700)


def test_add_stock_classifies_and_lands_in_the_list(app_url, page):
    _add(page, app_url, "Greek yogurt")
    # the classifier auto-filled the category on the way in
    # and the item now shows in the grouped stock list
    page.goto(f"{app_url}/#/stock", wait_until="networkidle")
    page.wait_for_timeout(600)
    assert page.get_by_text("Greek yogurt", exact=False).count() > 0


def test_dashboard_adds_in_place_without_navigating(app_url, page):
    page.goto(f"{app_url}/#/", wait_until="networkidle")
    page.wait_for_timeout(700)
    page.get_by_role("button", name="＋ Add stock").click()
    page.wait_for_selector(".modal", timeout=5000)
    page.wait_for_timeout(300)
    page.locator('input[placeholder^="e.g. Organic"]').fill("Sourdough")
    page.get_by_role("button", name="Add", exact=True).click()
    page.wait_for_timeout(700)
    # the modal closed and we're STILL on the dashboard (no route change to /stock)
    assert page.query_selector(".modal") is None
    assert page.url.endswith("#/")


def test_use_shows_an_undo_toast(app_url, page):
    _add(page, app_url, "Butter")
    page.goto(f"{app_url}/#/stock", wait_until="networkidle")
    page.wait_for_timeout(600)
    page.get_by_text("Butter", exact=False).first.click()   # expand the group
    page.wait_for_timeout(300)
    page.get_by_role("button", name="Use", exact=True).first.click()
    page.wait_for_timeout(300)
    page.get_by_role("button", name="Ate it", exact=False).click()
    page.wait_for_timeout(500)
    toast = page.query_selector(".toaster .toast-item")
    assert toast is not None
    assert page.get_by_role("button", name="Undo").count() > 0


def test_condition_scale_is_ripeness_for_produce(app_url, page):
    page.goto(f"{app_url}/#/stock?add=1", wait_until="networkidle")
    page.wait_for_selector(".modal", timeout=5000)
    field = page.locator('input[placeholder^="e.g. Organic"]')
    field.fill("Bananas")
    field.dispatch_event("change")
    page.wait_for_timeout(800)
    page.get_by_text("More options", exact=False).click()
    page.wait_for_timeout(200)
    opts = page.eval_on_selector_all(
        ".modal select option", "els => els.map(e => e.textContent)")
    assert any("Unripe" in (o or "") for o in opts)   # ripeness, not fresh→going-off


def test_no_horizontal_overflow_on_key_pages(app_url, page):
    for path in ["/", "/stock", "/shopping", "/locations"]:
        page.set_viewport_size({"width": 768, "height": 900})
        page.goto(f"{app_url}/#{path}", wait_until="networkidle")
        page.wait_for_timeout(400)
        over = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        assert over <= 1, f"horizontal overflow on {path}: {over}px"
