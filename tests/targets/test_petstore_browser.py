"""Browser/API acceptance checks against a deployed disposable Petstore."""
import os
from pathlib import Path

import pytest

expect = pytest.importorskip('playwright.sync_api').expect

BASE = os.environ.get('PETSTORE_BASE_URL', '').rstrip('/')
pytestmark = pytest.mark.skipif(not BASE, reason='Set PETSTORE_BASE_URL to disposable deployed Petstore')


@pytest.fixture
def api(playwright):
    context = playwright.request.new_context()
    yield context
    context.dispose()


@pytest.mark.parametrize('endpoint', ['categories', 'products', 'items', 'countries'])
def test_seed_catalog_api(api, endpoint):
    response = api.get(f'{BASE}/rest/{endpoint}', headers={'accept': 'application/json'})
    assert response.status == 200
    records = response.json()
    assert isinstance(records, list) and records
    assert all('id' in item for item in records)


def test_category_to_product_to_item_anonymous(page):
    page.goto(f'{BASE}/shopping/main.xhtml')
    page.get_by_role('link', name='Fish', exact=True).click()
    products = page.locator('a[href*="showitems.xhtml"]')
    expect(products.first).to_be_visible()
    products.first.click()
    items = page.locator('a[href*="showitem.xhtml"]')
    expect(items.first).to_be_visible()
    name = items.first.inner_text()
    items.first.click()
    expect(page.get_by_role('heading', name=name, exact=False)).to_be_visible()
    expect(page.locator('.add-to-cart')).to_have_count(0)


def test_invalid_login_does_not_authenticate(page):
    page.goto(f'{BASE}/shopping/signon.xhtml')
    page.locator('input[id$=":login"]').fill('wronguser')
    page.locator('input[id$=":password"]').fill('wrongpass')
    page.locator('form').filter(has=page.locator('input[id$=":login"]')).locator('input[type="submit"]').click()
    expect(page.locator('a[href*="showaccount.xhtml"]')).to_have_count(0)
    expect(page.locator('a[href*="signon.xhtml"]').first).to_be_visible()


def test_login_add_and_remove_cart_item(page):
    page.goto(f'{BASE}/shopping/signon.xhtml')
    page.locator('input[id$=":login"]').fill('user')
    page.locator('input[id$=":password"]').fill('user')
    page.locator('form').filter(has=page.locator('input[id$=":login"]')).locator('input[type="submit"]').click()
    page.get_by_role('link', name='Fish', exact=True).click()
    page.locator('a[href*="showitems.xhtml"]').first.click()
    page.locator('a[href*="showitem.xhtml"]').first.click()
    page.locator('.add-to-cart').click()
    expect(page.locator('.remove-from-cart')).to_have_count(1)
    expect(page.locator('.checkout-cart')).to_be_visible()
    screenshot = os.environ.get('HARNESS_SCREENSHOT_DIR')
    if screenshot:
        Path(screenshot).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(screenshot)/'petstore-cart.png'), full_page=True)
    page.locator('.remove-from-cart').click()
    expect(page.locator('.remove-from-cart')).to_have_count(0)
    expect(page.locator('.checkout-cart')).to_have_count(0)


def test_known_user_wrong_password_is_rejected(page):
    page.goto(f'{BASE}/shopping/signon.xhtml')
    page.locator('input[id$=":login"]').fill('user')
    page.locator('input[id$=":password"]').fill('wrongpass')
    page.locator('form').filter(has=page.locator('input[id$=":login"]')).locator('input[type="submit"]').click()
    expect(page.locator('a[href*="showaccount.xhtml"]')).to_have_count(0)
    expect(page.locator('a[href*="signon.xhtml"]').first).to_be_visible()
