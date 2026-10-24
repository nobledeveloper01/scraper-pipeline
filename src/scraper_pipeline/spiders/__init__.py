from .base import Fetcher, RobotsDisallowed, Spider
from .books import BooksSpider
from .pyjobs import PyJobsSpider
from .quotes_js import QuotesJsSpider

REGISTRY = {
    BooksSpider.name: BooksSpider,
    QuotesJsSpider.name: QuotesJsSpider,
    PyJobsSpider.name: PyJobsSpider,
}

__all__ = [
    "Fetcher",
    "RobotsDisallowed",
    "Spider",
    "BooksSpider",
    "QuotesJsSpider",
    "PyJobsSpider",
    "REGISTRY",
]
