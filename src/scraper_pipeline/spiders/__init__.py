from .base import Fetcher, RobotsDisallowed, Spider
from .books import BooksSpider
from .quotes_js import QuotesJsSpider

REGISTRY = {
    BooksSpider.name: BooksSpider,
    QuotesJsSpider.name: QuotesJsSpider,
}

__all__ = ["Fetcher", "RobotsDisallowed", "Spider", "BooksSpider", "QuotesJsSpider", "REGISTRY"]
