"""Сквозная проверка в браузере (Playwright), экран телефона.

Нужен запущенный сервер и пользователи vesovaya / admin:
    BASE_URL=http://localhost:8000 OP_PASS=... ADMIN_PASS=... python scripts/e2e_smoke.py
Скриншоты сохраняются в папку SHOTS (по умолчанию ./e2e-shots).
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("BASE_URL", "http://localhost:8000")
OP_PASS = os.environ.get("OP_PASS", "vesy2026")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin123")
SHOTS = Path(os.environ.get("SHOTS", "e2e-shots"))
SHOTS.mkdir(parents=True, exist_ok=True)


def step(msg):
    print(f"✓ {msg}", flush=True)


def login(page, user, pw):
    page.fill("#loginInput", user)
    page.fill("#passwordInput", pw)
    page.click("#loginForm button[type=submit]")
    expect(page.locator("#appView")).to_be_visible()


def main():
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, locale="ru-RU",
                                  timezone_id="Asia/Almaty")
        page = ctx.new_page()
        # «Failed to load resource» — ожидаемые 401 до входа и отказы сети в офлайн-сценарии
        page.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text
                and errors.append(m.text))
        page.on("pageerror", lambda e: errors.append(str(e)))

        # 1. Вход
        page.goto(BASE)
        expect(page.locator("#loginView")).to_be_visible()
        page.fill("#loginInput", "vesovaya"); page.fill("#passwordInput", "wrong")
        page.click("#loginForm button[type=submit]")
        expect(page.locator("#loginError")).to_have_text("Неверный логин или пароль")
        page.screenshot(path=SHOTS / "01-login.png")
        login(page, "vesovaya", OP_PASS)
        page.evaluate("navigator.serviceWorker.ready")
        step("вход: неверный пароль отклонён, верный пускает")

        # 2. Пустая форма — ошибки, данные не теряются (ZER-1, ZER-2)
        page.click("#tripForm button[type=submit]")
        expect(page.locator('[data-error-for="vehicle_id"]')).to_have_text("Выберите машину")
        expect(page.locator('[data-error-for="field_id"]')).to_have_text("Выберите поле")
        expect(page.locator('[data-error-for="gross_kg"]')).to_have_text("Введите брутто")
        step("пустая форма не сохраняется, поля подсвечены")

        # 3. Рейс: тара подставляется, нетто считается, сохраняется (ZER-3..7)
        page.click("label[for^=veh-]:has-text('МТЗ 82')")
        expect(page.locator("#tareInput")).to_have_value("5800")
        page.select_option("#fieldSelect", label="Березка")
        page.fill("#grossInput", "25000")
        expect(page.locator("#nettoOut")).to_have_text("19 200 кг")
        page.screenshot(path=SHOTS / "02-form-filled.png")
        page.click("#tripForm button[type=submit]")
        expect(page.locator("#toast")).to_have_text("Рейс сохранён")
        expect(page.locator("#tripLog tr")).to_have_count(1)
        expect(page.locator("#fieldSelect option:checked")).to_have_text("Березка")  # поле остаётся выбранным
        step("рейс сохранён: тара по машине, нетто 19 200 кг, в списке")

        # 4. Тара > брутто (ZER-6)
        page.click("label[for^=veh-]:has-text('МТЗ 952')")
        page.fill("#grossInput", "3000")
        expect(page.locator("#nettoOut")).to_contain_text("Тара должна быть меньше брутто")
        page.click("#tripForm button[type=submit]")
        expect(page.locator('[data-error-for="gross_kg"]')).to_contain_text("Тара должна быть меньше")
        expect(page.locator("#tripLog tr")).to_have_count(1)
        step("тара больше брутто — не сохраняется")

        # 5. Ручная тара
        page.click("#tareEditBtn")
        page.fill("#tareInput", "6000"); page.fill("#grossInput", "22500")
        expect(page.locator("#nettoOut")).to_have_text("16 500 кг")
        page.click("#tripForm button[type=submit]")
        expect(page.locator("#tripLog tr")).to_have_count(2)
        step("ручная тара работает")

        # 6. ОФЛАЙН: рейс сохраняется на телефоне и уходит, когда связь вернулась
        ctx.set_offline(True)
        page.click("label[for^=veh-]:has-text('МТЗ 80')")
        page.select_option("#fieldSelect", label="Ракета")
        page.fill("#grossInput", "21000")
        page.click("#tripForm button[type=submit]")
        expect(page.locator("#toast")).to_contain_text("сохранён на телефоне")
        expect(page.locator("#tripLog tr.pending")).to_have_count(1)
        expect(page.locator("#syncPill")).to_contain_text("1 не отправл")
        page.screenshot(path=SHOTS / "03-offline-pending.png")
        # перезагрузка без сети: оболочка из Service Worker, рейс из IndexedDB
        page.reload()
        expect(page.locator("#appView")).to_be_visible()
        expect(page.locator("#tripLog tr.pending")).to_have_count(1)
        step("без связи: рейс в очереди, переживает перезагрузку страницы")
        ctx.set_offline(False)
        page.evaluate("window.dispatchEvent(new Event('online'))")
        expect(page.locator("#tripLog tr.pending")).to_have_count(0, timeout=10000)
        expect(page.locator("#tripLog tr")).to_have_count(3)
        expect(page.locator("#syncPill")).to_contain_text("На связи")
        step("связь вернулась: рейс ушёл на сервер, дублей нет")

        # 7. Исправление (ZER-9, ZER-10)
        page.locator("#tripLog tr").first.click()
        expect(page.locator("#editDialog")).to_be_visible()
        page.fill("#editGross", "24000")
        expect(page.locator("#editNetto")).to_have_text("18 200 кг")
        page.screenshot(path=SHOTS / "04-edit.png")
        page.click("#editSave")
        expect(page.locator("#toast")).to_have_text("Исправление сохранено")
        expect(page.locator("#tripLog tr").first.locator(".badge.corrected")).to_be_visible()
        page.screenshot(path=SHOTS / "05-list.png", full_page=True)
        step("исправление пересчитало нетто и поставило отметку «испр.»")

        # 8. Итог дня (ZER-11..13): 18 200 + 16 500 + 15 300 = 50 000
        page.click("nav button[data-tab=day]")
        expect(page.locator("#dayTotal")).to_have_text("50,0")
        expect(page.locator("#dayTrips")).to_contain_text("3 рейса")
        expect(page.locator("#byVehicle .bar-row")).to_have_count(4)
        expect(page.locator("#byVehicle .bar-row.zero")).to_have_count(1)
        page.screenshot(path=SHOTS / "06-day.png", full_page=True)
        step("итог дня 50,0 т, по машинам (включая нулевую) и по полям")

        # 9. Сезон
        page.click("nav button[data-tab=season]")
        expect(page.locator("#seasonTotal")).to_have_text("50,0")
        step("накопительный итог за сезон")

        # 10. Казахский язык (НФТ-08)
        page.click("nav button[data-tab=entry]")
        page.locator("#appView .lang-toggle").click()
        expect(page.locator("nav button[data-tab=entry]")).to_have_text("Өлшеу")
        expect(page.locator("#tripForm button[type=submit]")).to_have_text("Рейсті сақтау")
        page.screenshot(path=SHOTS / "07-kazakh.png")
        page.locator("#appView .lang-toggle").click()
        step("переключение RU/KZ без перезагрузки")

        # 11. Оператор не видит вкладку «Машины» (НФТ-04)
        expect(page.locator("nav button[data-tab=vehicles]")).to_be_hidden()
        page.click("#logoutBtn")
        expect(page.locator("#loginView")).to_be_visible()

        # 12. Администратор меняет тару
        login(page, "admin", ADMIN_PASS)
        page.click("nav button[data-tab=vehicles]")
        form = page.locator("form[data-vehicle]").filter(has_text="МТЗ 1025")
        form.locator("input").fill("6550")
        form.locator("button").click()
        expect(page.locator("#toast")).to_contain_text("Сохранено")
        page.screenshot(path=SHOTS / "08-vehicles-admin.png")
        page.click("nav button[data-tab=entry]")
        page.click("label[for^=veh-]:has-text('МТЗ 1025')")
        expect(page.locator("#tareInput")).to_have_value("6550")
        step("администратор изменил тару — подставляется в новую форму")

        # 13. Ввод только с клавиатуры (НФТ-09)
        page.locator('input[name="vehicle"]').first.focus()
        page.keyboard.press("ArrowRight")           # МТЗ 82
        page.keyboard.press("Tab")                  # поле
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Tab")
        page.keyboard.type("20000")
        page.keyboard.press("Enter")
        expect(page.locator("#toast")).to_have_text("Рейс сохранён")
        step("рейс создан только с клавиатуры")

        # Десктоп
        page.set_viewport_size({"width": 1280, "height": 900})
        page.screenshot(path=SHOTS / "09-desktop.png", full_page=True)

        browser.close()

    if errors:
        print("Ошибки в консоли браузера:", *errors, sep="\n  ")
        sys.exit(1)
    print("Все проверки пройдены, ошибок в консоли нет.")


if __name__ == "__main__":
    main()
