from .base import Fetcher, RobotsDisallowed, Spider
from .books import BooksSpider
from .listings import ListingsSpider
from .pyjobs import PyJobsSpider
from .quotes_js import QuotesJsSpider

REGISTRY = {
    BooksSpider.name: BooksSpider,
    QuotesJsSpider.name: QuotesJsSpider,
    PyJobsSpider.name: PyJobsSpider,
    ListingsSpider.name: ListingsSpider,
}

__all__ = [
    "Fetcher",
    "RobotsDisallowed",
    "Spider",
    "BooksSpider",
    "QuotesJsSpider",
    "PyJobsSpider",
    "ListingsSpider",
    "REGISTRY",
]
