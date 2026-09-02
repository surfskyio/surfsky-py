"""Selenium over the session's own ChromeDriver. Not recommended: WebDriver
leaves traces a site can read. Prefer ``client.browser()``, or Playwright over CDP.

    pip install selenium
    export SURFSKY_API_TOKEN=... SURFSKY_API_BASE_URL=...
    uv run python examples/selenium_connect.py
"""

from selenium import webdriver  # ty: ignore[unresolved-import]

from surfsky import Surfsky


def main() -> None:
    with Surfsky() as client, client.session(enable_chromedriver=True) as session:
        driver = webdriver.Remote(
            command_executor=f"{client.base_url}/chromedriver/{session.internal_uuid}",
            options=webdriver.ChromeOptions(),
        )
        try:
            driver.get("https://example.com")
            print(driver.title)
            print(driver.find_element("css selector", "h1").text)
        finally:
            driver.quit()


if __name__ == "__main__":
    main()
