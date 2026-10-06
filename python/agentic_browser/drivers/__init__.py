from .types import DRIVER_LABELS, DRIVER_NAMES, BrowserDriver, DriverName, ElementInfo, LaunchOptions, PageSnapshot

__all__ = ["DRIVER_LABELS", "DRIVER_NAMES", "BrowserDriver", "DriverName", "ElementInfo", "LaunchOptions", "PageSnapshot", "create_driver"]


def create_driver(name: DriverName) -> BrowserDriver:
    """Imports only the selected driver's library."""
    if name == "playwright":
        from .playwright_driver import PlaywrightDriver

        return PlaywrightDriver()
    if name == "selenium":
        from .selenium_driver import SeleniumDriver

        return SeleniumDriver()
    if name == "standard":
        from .playwright_driver import StandardBrowserDriver

        return StandardBrowserDriver()
    raise ValueError(f'Unknown driver "{name}"')
