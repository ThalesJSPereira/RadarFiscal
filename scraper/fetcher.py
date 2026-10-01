"""Camada simples de download de páginas, com tratamento de erro amigável."""

import requests

from . import config


class FetchError(Exception):
    pass


def fetch(url):
    try:
        resp = requests.get(
            url,
            headers=config.HTTP_HEADERS,
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        resp.encoding = resp.encoding or "utf-8"
        return resp.text
    except requests.RequestException as exc:
        raise FetchError(f"Falha ao acessar {url}: {exc}") from exc
